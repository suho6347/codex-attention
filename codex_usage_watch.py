"""Observe Codex Desktop's existing local quota records. No network or AI calls."""
from __future__ import annotations

import argparse
import contextlib
import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import msvcrt
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

from usage_attribution import Attribution

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'usage-watch'
CONFIG = ROOT / 'usage-watch-config.json'
SESSIONS = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'sessions'
DEFAULTS = dict(poll_seconds=5, fast_seconds_per_percent=600,
                release_seconds_per_percent=800,
                history_seconds=21600, stale_seconds=120,
                edge_lease_seconds=20, guard_poll_seconds=3,
                status_seconds=30, attribution_window_seconds=300)


def stop_event_name(root):
    identity = hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()[:16]
    return 'Local\\AgentLab.CodexUsage.Stop.' + identity


STOP_EVENT = stop_event_name(ROOT)


def event_kernel():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.FindFirstChangeNotificationW.argtypes = [w.LPCWSTR, w.BOOL, w.DWORD]
    kernel.FindFirstChangeNotificationW.restype = w.HANDLE
    kernel.FindNextChangeNotification.argtypes = [w.HANDLE]
    kernel.FindCloseChangeNotification.argtypes = [w.HANDLE]
    kernel.CreateEventW.argtypes = [w.LPVOID, w.BOOL, w.BOOL, w.LPCWSTR]
    kernel.CreateEventW.restype = w.HANDLE
    kernel.OpenEventW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
    kernel.OpenEventW.restype = w.HANDLE
    kernel.SetEvent.argtypes = kernel.ResetEvent.argtypes = [w.HANDLE]
    kernel.WaitForMultipleObjects.argtypes = [w.DWORD, ctypes.POINTER(w.HANDLE), w.BOOL, w.DWORD]
    kernel.WaitForMultipleObjects.restype = w.DWORD
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.WaitForSingleObject.restype = w.DWORD
    kernel.CloseHandle.argtypes = [w.HANDLE]
    return kernel


def signal_stop(name=STOP_EVENT):
    kernel = event_kernel()
    handle = kernel.OpenEventW(2, False, name)
    if not handle:
        return False
    try:
        if not kernel.SetEvent(handle):
            raise ctypes.WinError(ctypes.get_last_error())
        return True
    finally:
        kernel.CloseHandle(handle)


class Wakeups:
    """Wait in the kernel for directory changes, stop requests, or one timeout."""

    def __init__(self, root, stop_name=STOP_EVENT):
        self.kernel = event_kernel()
        self.names = self.content = self.stop = None
        try:
            self.names = self.kernel.FindFirstChangeNotificationW(str(root), True, 0x1 | 0x2)
            self.content = self.kernel.FindFirstChangeNotificationW(str(root), True, 0x8 | 0x10)
            invalid = ctypes.c_void_p(-1).value
            if self.names == invalid or self.content == invalid:
                raise ctypes.WinError(ctypes.get_last_error())
            self.stop = self.kernel.CreateEventW(None, True, False, stop_name)
            if not self.stop or not self.kernel.ResetEvent(self.stop):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def wait(self, seconds, include_source=True):
        handles = [self.stop] + ([self.names, self.content] if include_source else [])
        milliseconds = min(0xfffffffe, max(0, math.ceil(seconds * 1000)))
        result = self.kernel.WaitForMultipleObjects(len(handles), (w.HANDLE * len(handles))(*handles), False, milliseconds)
        if result == 0:
            return 'stop'
        if result < len(handles):
            return 'source'
        if result == 258:
            return 'timeout'
        raise ctypes.WinError(ctypes.get_last_error())

    def take_changes(self):
        discover = False
        for handle in (self.names, self.content):
            result = self.kernel.WaitForSingleObject(handle, 0)
            if result == 0:
                discover |= handle == self.names
                if not self.kernel.FindNextChangeNotification(handle):
                    raise ctypes.WinError(ctypes.get_last_error())
            elif result != 258:
                raise ctypes.WinError(ctypes.get_last_error())
        return discover

    def close(self):
        for handle in (self.names, self.content):
            if handle and handle != ctypes.c_void_p(-1).value:
                self.kernel.FindCloseChangeNotification(handle)
        if self.stop:
            self.kernel.CloseHandle(self.stop)
        self.names = self.content = self.stop = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


@dataclass
class Sample:
    at: float
    used: float
    limit_id: str
    window: str
    minutes: int
    reset: int

    @property
    def key(self):
        return f'{self.limit_id}:{self.window}:{self.minutes}'


@dataclass
class Track:
    reset: int
    at: float
    used: float
    last_change_at: float | None = None
    last_drop_at: float | None = None
    active: bool = False
    kind: str = 'observing'
    rate: float | None = None


class Detector:
    def __init__(self, config):
        self.config = config
        self.tracks = {}

    def observe(self, sample):
        c = self.config
        old = self.tracks.get(sample.key)
        # Reset timestamps can differ by a few seconds between app processes.
        if old is not None and sample.reset < old.reset - 120:
            return None
        if old is None or abs(old.reset - sample.reset) > 120:
            self.tracks[sample.key] = Track(sample.reset, sample.at, sample.used)
            return None
        if sample.at <= old.at:
            return None
        # Concurrent chats can replay an older cached value. Never count its rebound.
        if sample.used < old.used:
            return None
        gap = sample.at - old.at > c['stale_seconds'] or old.kind == 'stale'
        if gap:
            # A missing observation cannot anchor an exact 1% crossing.
            self.mark_stale(old)
            old.kind = 'observing'
        gain = sample.used - old.used
        previous_at = old.at
        old.at = sample.at
        old.used = sample.used
        if gain <= 0:
            return None
        previous_change = old.last_change_at
        old.last_change_at = None if gap else sample.at
        old.last_drop_at = sample.at
        interval_rate = 60 * gain / (sample.at - previous_change) if previous_change is not None else None
        # A multi-point drop contains at least gain-1 real percentage points,
        # even if the preceding integer value was just below its next boundary.
        burst_rate = 60 * (gain - 1) / (sample.at - previous_at) if gain > 1 else None
        rates = [rate for rate in (interval_rate, burst_rate) if rate is not None]
        if not rates:
            return None
        old.rate = max(rates)
        was_active = old.active
        if old.active and old.rate <= 60 / c['release_seconds_per_percent']:
            old.active = False
        elif not old.active and old.rate >= 60 / c['fast_seconds_per_percent']:
            old.active = True
        old.kind = 'fast' if old.active else 'normal'
        return self.signal(sample.key, old, sample.at) if old.active and not was_active else None

    @staticmethod
    def mark_stale(track):
        track.active, track.kind = False, 'stale'
        track.last_change_at, track.last_drop_at, track.rate = None, None, None

    def signal(self, key, track, now):
        rate = track.rate
        if rate is not None and track.last_drop_at is not None:
            # While the displayed percentage stays flat, the maximum rate
            # consistent with not crossing the next 1% boundary falls with time.
            elapsed = now-track.last_drop_at
            if elapsed > 0:
                rate = min(rate, 60 / elapsed)
        return dict(kind='fast', key=key, used=track.used, remaining=100-track.used,
                    rate_pp_per_min=rate,
                    seconds_per_percent=60/rate if rate else None, at=now)

    def current(self, now):
        active = []
        for key, track in self.tracks.items():
            if now-track.at >= self.config['stale_seconds']:
                self.mark_stale(track)
                continue
            if not track.active:
                track.kind = 'normal' if track.rate is not None else 'observing'
                continue
            signal = self.signal(key, track, now)
            if signal['rate_pp_per_min'] is None or signal['rate_pp_per_min'] <= 60 / self.config['release_seconds_per_percent']:
                track.active, track.kind = False, 'normal'
            else:
                active.append(signal)
        return max(active, key=lambda s: s['rate_pp_per_min']) if active else None

    def next_deadline(self):
        deadlines = []
        for track in self.tracks.values():
            if track.kind != 'stale':
                deadlines.append(track.at + self.config['stale_seconds'])
            if track.active and track.last_drop_at is not None:
                deadlines.append(track.last_drop_at + self.config['release_seconds_per_percent'])
        return min(deadlines) if deadlines else None


def parse_line(line):
    if b'"token_count"' not in line:
        return []
    try:
        event = json.loads(line)
        return parse_event(event)
    except (ValueError, TypeError):
        return []


def parse_event(event):
    try:
        payload = event.get('payload', {})
        if event.get('type') != 'event_msg' or payload.get('type') != 'token_count':
            return []
        limits = payload.get('rate_limits') or {}
        at = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00')).timestamp()
        result = []
        for key in ('primary', 'secondary'):
            window = limits.get(key)
            if not isinstance(window, dict):
                continue
            used, minutes, reset = (window.get(k) for k in ('used_percent', 'window_minutes', 'resets_at'))
            if (isinstance(used, (float, int)) and not isinstance(used, bool)
                    and math.isfinite(used) and 0 <= used <= 100
                    and isinstance(minutes, int) and minutes > 0
                    and isinstance(reset, int) and reset > at):
                result.append(Sample(at, used, limits.get('limit_id') or 'codex', key, minutes, reset))
        return result
    except (ValueError, KeyError, TypeError, AttributeError):
        return []


class Reader:
    def __init__(self, root, history_seconds=21600, attribution=None):
        self.root = root
        self.history_seconds = history_seconds
        self.files = {}
        self.attribution = attribution

    def consume(self, line, path, now):
        # Quota and attribution share the same read and JSON parse. Message
        # contents and tool outputs are never decoded for this feature.
        if b'"token_count"' not in line and (self.attribution is None or b'"turn_context"' not in line):
            return []
        try:
            event = json.loads(line)
            if self.attribution is not None:
                self.attribution.observe(path, event, now)
            return [s for s in parse_event(event) if now-self.history_seconds <= s.at <= now+5]
        except (ValueError, TypeError, AttributeError):
            return []

    def scan(self, now, discover=True):
        samples = []
        paths = list(self.root.rglob('rollout-*.jsonl')) if discover else list(self.files)
        if discover:
            existing = set(paths)
            self.files = {path: cursor for path, cursor in self.files.items() if path in existing}
            if self.attribution is not None:
                self.attribution.logs = {path: state for path, state in self.attribution.logs.items() if path in existing}
        for path in paths:
            try:
                cursor = self.files.get(path)
                if cursor is not None and not cursor[2]:
                    continue
                stat = path.stat()
                if cursor is None:
                    with path.open('rb') as stream:
                        meta = json.loads(stream.readline())
                        desktop = meta.get('payload', {}).get('originator') == 'Codex Desktop'
                        partial = b''
                        if desktop and self.attribution is not None:
                            self.attribution.register(path, meta.get('payload', {}))
                        if desktop and stat.st_mtime >= now-self.history_seconds:
                            # Once at startup, recover the latest quota changes
                            # records without parsing or saving chat contents.
                            for line in stream:
                                if not line.endswith(b'\n'):
                                    partial = line
                                    break
                                samples.extend(self.consume(line, path, now))
                        else:
                            # Old files cannot contain recent events. Keep an
                            # append cursor so an old chat can resume normally.
                            stream.seek(0, 2)
                        self.files[path] = [stream.tell(), partial, desktop]
                    continue
                else:
                    discard = False
                if not cursor[2] or cursor[0] == stat.st_size:
                    continue
                if cursor[0] > stat.st_size or stat.st_size - cursor[0] > 4194304:
                    cursor[0], cursor[1] = max(0, stat.st_size - 524288), b''
                    discard = cursor[0] > 0
                with path.open('rb') as stream:
                    stream.seek(cursor[0])
                    data = cursor[1] + stream.read()
                    cursor[0] = stream.tell()
                if discard:
                    data = data.partition(b'\n')[2]
                lines = data.split(b'\n')
                cursor[1] = lines.pop()
                for line in lines:
                    samples.extend(self.consume(line, path, now))
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        if self.attribution is not None:
            self.attribution.prune(now)
        return sorted(samples, key=lambda s: s.at)


def read_config():
    c = DEFAULTS | (json.loads(CONFIG.read_text('utf-8')) if CONFIG.exists() else {})
    for key in DEFAULTS:
        if isinstance(c[key], bool) or not isinstance(c[key], (int, float)) or not math.isfinite(c[key]) or c[key] <= 0:
            raise ValueError(f'Invalid setting: {key}')
    if c['poll_seconds'] < 1 or c['edge_lease_seconds'] < c['poll_seconds']*2:
        raise ValueError('Invalid polling interval or edge lease')
    if c['release_seconds_per_percent'] <= c['fast_seconds_per_percent']:
        raise ValueError('Release interval must exceed the fast interval')
    return c


def save_status(data, filename='status.json'):
    temp = STATE / (filename + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), 'utf-8')
    temp.replace(STATE / filename)


def acquire_lock(filename='monitor.lock'):
    STATE.mkdir(exist_ok=True)
    handle = (STATE / filename).open('a+b')
    if handle.tell() == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return handle
    except OSError:
        handle.close()
        return None


def running(filename='monitor.lock'):
    handle = acquire_lock(filename)
    if handle is None:
        return True
    handle.close()
    return False


def launch_independent():
    # A normal child inherits Codex's outer kill-on-close job even when the
    # inner command job allows breakaway. Windows process creation through CIM
    # gives this user-requested monitor an independent lifetime, without a
    # service, scheduled task, startup entry, credentials, or network listener.
    quote = lambda text: "'" + str(text).replace("'", "''") + "'"
    python = Path(sys.executable).with_name('python.exe')
    command = subprocess.list2cmdline([str(python), str(Path(__file__).resolve()), 'run'])
    script = (
        "$ErrorActionPreference='Stop'; "
        "$startup=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly "
        "-Property @{ShowWindow=[uint16]0;CreateFlags=[uint32]0x09000000}; "
        "$result=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{"
        f"CommandLine={quote(command)};CurrentDirectory={quote(ROOT)};"
        "ProcessStartupInformation=$startup}; "
        "if($result.ReturnValue -ne 0){throw ('Process creation failed: '+$result.ReturnValue)}; "
        "$result.ProcessId"
    )
    shell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-Command', script],
                   capture_output=True, text=True, timeout=20, check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)


def codex_desktop_running():
    """Match the installed desktop package, excluding standalone Codex CLI."""
    import ctypes
    from ctypes import wintypes as w

    cached = getattr(codex_desktop_running, '_cached', None)
    if cached is not None:
        cached_kernel, cached_process = cached
        code = w.DWORD()
        if cached_kernel.GetExitCodeProcess(cached_process, ctypes.byref(code)) and code.value == 259:
            return True
        cached_kernel.CloseHandle(cached_process)
        codex_desktop_running._cached = None

    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD),
                    ('th32ProcessID', w.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', w.DWORD), ('cntThreads', w.DWORD),
                    ('th32ParentProcessID', w.DWORD), ('pcPriClassBase', w.LONG),
                    ('dwFlags', w.DWORD), ('szExeFile', w.WCHAR * 260)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        valid = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while valid:
            if entry.szExeFile.casefold() in ('chatgpt.exe', 'codex.exe'):
                process = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
                if process:
                    try:
                        path = ctypes.create_unicode_buffer(32768)
                        length = w.DWORD(len(path))
                        if kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(length)):
                            if '\\windowsapps\\openai.codex_' in path.value.casefold():
                                codex_desktop_running._cached = (kernel, process)
                                process = None
                                return True
                    finally:
                        if process:
                            kernel.CloseHandle(process)
            valid = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        return False
    finally:
        kernel.CloseHandle(snapshot)


def guard():
    lock = acquire_lock('guard.lock')
    if lock is None:
        return
    config = read_config()
    signature, next_status = None, 0
    try:
        while True:
            now = time.time()
            active = codex_desktop_running()
            paused = (STATE / 'paused.flag').exists()
            monitor = running()
            error = None
            if active and not paused and not monitor:
                try:
                    launch_independent()
                    monitor = running()
                except (OSError, subprocess.SubprocessError) as failure:
                    error = f'{type(failure).__name__}: {failure}'
            elif (not active or paused) and monitor:
                (STATE / 'stop.request').touch()
                signal_stop()
            current_signature = (active, paused, monitor, error)
            if current_signature != signature or time.monotonic() >= next_status:
                save_status(dict(state='paused' if paused else ('watching' if active else 'waiting_for_codex'),
                                 pid=os.getpid(), updated_at=now, codex_open=active,
                                 monitor_running=monitor, last_error=error), 'guard-status.json')
                signature, next_status = current_signature, time.monotonic() + config['status_seconds']
            time.sleep(config['guard_poll_seconds'])
    finally:
        cached = getattr(codex_desktop_running, '_cached', None)
        if cached is not None:
            cached[0].CloseHandle(cached[1])
            codex_desktop_running._cached = None
        lock.close()


def alert(signal, config, control_path, token):
    message = f"할당량이 빠르게 줄고 있습니다. 남은 할당량 {signal['remaining']:g}%."
    python = Path(sys.executable).with_name('python.exe')
    result = subprocess.run([str(python), str(ROOT / 'notify_attention.py'), message,
                             '--title', 'Codex — 소모속도 상승', '--color', 'red',
                             '--while-active', str(control_path), '--lease-token', token],
                            capture_output=True, text=True, encoding='utf-8', timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW, check=True,
                            env=os.environ | {'PYTHONIOENCODING': 'utf-8'})
    return json.loads(result.stdout)


def process_alive(pid):
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = w.DWORD()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


class EdgeController:
    def __init__(self, config, state=STATE, attribution=None):
        self.config, self.state = config, state
        self.path = state / 'edge-control.json'
        self.active, self.window, self.token = False, None, None
        self.last_write_at = None
        self.attribution = attribution

    def write(self, active, now):
        data = dict(active=active, token=self.token, updated_at=now,
                    valid_until=now+self.config['edge_lease_seconds'])
        if active and self.attribution is not None:
            data['contributors'] = self.attribution.snapshot(now)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(data), 'utf-8')
        temp.replace(self.path)
        self.last_write_at = now

    def update(self, signal, now):
        if signal is None:
            if self.active or self.last_write_at is None:
                self.write(False, now)
            self.active = False
            return None
        due = self.last_write_at is None or now >= self.last_write_at + self.config['edge_lease_seconds']/2
        spawn = not self.active or not self.window or (due and not process_alive(self.window['pid']))
        if not spawn and not due:
            return None
        if spawn:
            self.token = f'{os.getpid()}:{time.time_ns()}'
        self.write(True, now)
        if spawn:
            self.window = alert(signal, self.config, self.path, self.token)
            self.active = True
            return self.window
        self.active = True
        return None

    def next_deadline(self):
        return self.last_write_at + self.config['edge_lease_seconds']/2 if self.active else None

    def close(self):
        self.update(None, time.time())


def run():
    lock = acquire_lock()
    if lock is None:
        return
    started = time.time()
    config = read_config()
    attribution = Attribution(SESSIONS.parent, config['attribution_window_seconds'])
    reader, detector = Reader(SESSIONS, config['history_seconds'], attribution), Detector(config)
    edge = EdgeController(config, attribution=attribution)
    stop = STATE / 'stop.request'
    last_signal = None
    last_error = None
    count = 0
    try:
        with Wakeups(SESSIONS) as wakeups:
            pending, discover = True, True
            next_scan, next_status, signature = 0, 0, None
            while not (stop.exists() and stop.stat().st_mtime >= started):
                now = time.time()
                if time.monotonic() >= next_scan:
                    discover |= wakeups.take_changes()
                    for sample in reader.scan(now, discover=discover):
                        detector.current(sample.at)
                        detector.observe(sample)
                    pending, discover = False, False
                    next_scan = time.monotonic() + config['poll_seconds']
                now = time.time()
                signal = detector.current(now)
                try:
                    notification = edge.update(signal, now)
                    last_error = None
                    if notification:
                        last_signal = signal | {'notification': notification}
                        count += 1
                        with (STATE / 'alerts.jsonl').open('a', encoding='utf-8') as stream:
                            stream.write(json.dumps(last_signal, ensure_ascii=False) + '\n')
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    last_error = f'{type(error).__name__}: {error}'
                current_signature = (tuple((key, t.used, t.kind, t.active, t.rate) for key, t in detector.tracks.items()),
                                     edge.active, edge.window['pid'] if edge.window else None, count, last_error)
                if current_signature != signature or time.monotonic() >= next_status:
                    tracks = {key: dict(used=t.used, remaining=100-t.used, observed_at=t.at,
                                       state=t.kind,
                                       observed_pp_per_min=detector.signal(key, t, now)['rate_pp_per_min'],
                                       seconds_per_percent=detector.signal(key, t, now)['seconds_per_percent'])
                              for key, t in detector.tracks.items()}
                    save_status(dict(state='running', pid=os.getpid(), started_at=started,
                                     updated_at=now, source='Codex Desktop local session records',
                                     monitoring='filesystem_events_with_size_check', network_calls=False, ai_calls=False, config=config,
                                     tracks=tracks, alert_count=count, last_alert=last_signal,
                                     edge=dict(active=edge.active, window_pid=edge.window['pid'] if edge.window else None),
                                     last_error=last_error))
                    signature, next_status = current_signature, time.monotonic() + config['status_seconds']
                # One kernel wait covers the next file event, stop request,
                # freshness/rate expiry, status heartbeat, and active edge lease.
                delays = [max(0, next_status-time.monotonic())]
                # NTFS can defer notifications while Codex keeps a log open.
                # Check cached file sizes, never rescan folders on this timer.
                delays.append(max(0, next_scan-time.monotonic()))
                for deadline in (detector.next_deadline(), edge.next_deadline()):
                    if deadline is not None:
                        delays.append(max(0, deadline-time.time()))
                reason = wakeups.wait(min(delays), include_source=not pending)
                if reason == 'stop':
                    break
                if reason == 'source':
                    pending = True
    finally:
        edge.close()
        save_status(dict(state='stopped', pid=os.getpid(), updated_at=time.time()))
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='status', choices=('start', 'stop', 'status', 'run', 'guard', 'probe'))
    action = parser.parse_args().action
    STATE.mkdir(exist_ok=True)
    if action in ('run', 'guard'):
        logfile = 'guard.log' if action == 'guard' else 'process.log'
        with (STATE / logfile).open('a', encoding='utf-8', buffering=1) as log:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                try:
                    (guard if action == 'guard' else run)()
                except Exception:
                    traceback.print_exc()
                    raise SystemExit(1)
    elif action == 'start':
        (STATE / 'paused.flag').unlink(missing_ok=True)
        if not running():
            read_config()
            launch_independent()
            for _ in range(30):
                time.sleep(.1)
                if running():
                    break
        print(json.dumps({'running': running(), 'status_file': str(STATE / 'status.json')}, ensure_ascii=False))
    elif action == 'stop':
        (STATE / 'paused.flag').touch()
        (STATE / 'stop.request').touch()
        signal_stop()
        print(json.dumps({'stop_requested': True, 'exit_within_seconds': read_config()['poll_seconds'] + 1}))
    elif action == 'probe':
        samples = Reader(SESSIONS).scan(time.time())
        latest = {s.key: dict(used=s.used, remaining=100-s.used, observed_at=s.at) for s in samples}
        print(json.dumps({'source': 'Codex Desktop local session records', 'latest': latest}, ensure_ascii=False))
    else:
        status = STATE / 'status.json'
        data = json.loads(status.read_text('utf-8')) if status.exists() else {}
        guard_status = STATE / 'guard-status.json'
        guardian = json.loads(guard_status.read_text('utf-8')) if guard_status.exists() else None
        print(json.dumps(data | {'running': running(), 'autostart_running': running('guard.lock'),
                                 'autostart': guardian}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
