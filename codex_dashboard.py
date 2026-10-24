"""Read local Codex activity and serve a read-only dashboard on loopback."""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import webbrowser

from codex_usage_watch import Detector, parse_event, read_config
from usage_attribution import Attribution, clean_title, usage_vector

ROOT = Path(__file__).resolve().parent
WEB = ROOT / 'web'
STATE = ROOT / 'dashboard-state'
TAIL_BYTES = 1024 * 1024
MAX_LINE = 2 * 1024 * 1024
READ_BYTES = 4 * 1024 * 1024
STATES = ('running', 'waiting', 'completed', 'interrupted', 'error', 'stale', 'idle')


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        return result if math.isfinite(result) else 0
    except (ValueError, TypeError, AttributeError, OverflowError):
        return 0


def read_json(path):
    try:
        value = json.loads(path.read_text('utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def catalog(home, limit=500):
    """Select metadata only. Never select prompts, previews, auth, or messages."""
    databases = sorted(home.glob('state_*.sqlite'),
                       key=lambda p: int(p.stem.split('_')[-1])
                       if p.stem.split('_')[-1].isdigit() else -1, reverse=True)
    if databases:
        try:
            with closing(sqlite3.connect(databases[0].as_uri() + '?mode=ro',
                                         uri=True, timeout=.1)) as connection:
                connection.row_factory = sqlite3.Row
                columns = {r[1] for r in connection.execute('PRAGMA table_info(threads)')}
                # Older databases do not have every optional column.
                names = ('id', 'name', 'cwd', 'model', 'updated_at', 'archived',
                         'originator', 'agent_nickname', 'git_branch', 'tokens_used')
                selected = ', '.join(n if n in columns else f'NULL AS {n}' for n in names)
                rows = connection.execute(
                    f'SELECT {selected} FROM threads ORDER BY updated_at DESC LIMIT ?', (limit,))
                return {r['id']: dict(r) for r in rows}, None
        except sqlite3.Error:
            warning = '채팅 DB를 읽을 수 없어 세션 기록의 메타데이터를 사용합니다.'
    else:
        warning = None
    values = {}
    try:
        with (home / 'session_index.jsonl').open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict) and isinstance(row.get('id'), str):
                        values[row['id']] = dict(id=row['id'], name=row.get('thread_name'),
                                                 updated_at=timestamp(row.get('updated_at')))
                except ValueError:
                    continue
    except OSError:
        pass
    return values, warning


@dataclass
class Session:
    id: str
    title: str = ''
    cwd: str = ''
    model: str = ''
    originator: str = ''
    parent_id: str | None = None
    branch: str = ''
    state: str = 'idle'
    activity: str = '아직 상태 기록이 없습니다'
    last_at: float = 0
    state_at: float = 0
    started_at: float = 0
    ended_at: float = 0
    turn_id: str | None = None
    tokens: tuple | None = None
    partial: bool = False
    born: float = 0
    pending: dict = field(default_factory=dict)
    events: deque = field(default_factory=lambda: deque(maxlen=8))

    def change(self, state, activity, at):
        if state != self.state or activity != self.activity:
            self.events.append(dict(state=state, activity=activity, at=at))
        self.state, self.activity, self.state_at = state, activity, at

    def observe(self, event):
        at = timestamp(event.get('timestamp'))
        if not at or at < max(self.last_at, self.born):
            return
        payload = event.get('payload')
        if not isinstance(payload, dict):
            return
        kind, sub = event.get('type'), payload.get('type')
        if kind == 'turn_context':
            self.model = str(payload.get('model') or self.model)
            self.cwd = str(payload.get('cwd') or self.cwd)
        elif kind == 'event_msg':
            if sub in ('task_started', 'turn_started'):
                self.turn_id = payload.get('turn_id')
                self.started_at, self.ended_at = at, 0
                self.pending.clear()
                self.change('running', '작업을 시작했습니다', at)
            elif sub in ('task_complete', 'task_completed', 'turn_completed', 'turn_aborted', 'task_failed'):
                turn = payload.get('turn_id')
                if turn and self.turn_id and turn != self.turn_id:
                    return
                self.ended_at = at
                self.pending.clear()
                state = ('interrupted' if sub == 'turn_aborted' else
                         'error' if payload.get('error') or sub == 'task_failed' else 'completed')
                activity = {'interrupted': '작업이 중단되었습니다', 'error': '오류로 작업이 종료되었습니다',
                            'completed': '응답이 완료되었습니다'}[state]
                self.change(state, activity, at)
            elif sub == 'token_count':
                info = payload.get('info')
                if isinstance(info, dict):
                    self.tokens = usage_vector(info.get('total_token_usage')) or self.tokens
            elif sub in ('request_user_input', 'approval_requested'):
                self.change('waiting', '사용자 확인을 기다립니다', at)
        elif kind == 'response_item':
            if sub in ('function_call', 'custom_tool_call'):
                name = str(payload.get('name') or 'tool')[:100]
                call = payload.get('call_id')
                waiting = name.rsplit('.', 1)[-1] in ('request_user_input', 'request_user_input_async')
                if call:
                    self.pending[call] = waiting
                    # Bound damaged logs with unpaired tool calls.
                    if len(self.pending) > 128:
                        self.pending.pop(next(iter(self.pending)))
                self.change('waiting' if waiting else 'running',
                            '사용자 확인을 기다립니다' if waiting else f'도구 실행 · {name}', at)
            elif sub in ('function_call_output', 'custom_tool_call_output'):
                self.pending.pop(payload.get('call_id'), None)
                if self.state in ('running', 'waiting', 'idle'):
                    waiting = any(self.pending.values())
                    self.change('waiting' if waiting else 'running',
                                '사용자 확인을 기다립니다' if waiting else '도구 결과를 처리하고 있습니다', at)
            elif sub == 'reasoning':
                self.change('running', '생각하고 있습니다', at)
            elif sub == 'message' and payload.get('role') == 'assistant':
                if payload.get('phase') == 'final_answer':
                    self.ended_at = at
                    self.pending.clear()
                    self.change('completed', '응답이 완료되었습니다', at)
                elif self.state not in ('completed', 'interrupted', 'error'):
                    self.change('running', '응답을 작성하고 있습니다', at)
        else:
            return
        self.last_at = max(self.last_at, at)

    def snapshot(self, meta, now, stale_seconds):
        state, activity = self.state, self.activity
        if state in ('running', 'waiting') and now - self.last_at >= stale_seconds:
            state, activity = 'stale', '기록이 끊겨 현재 상태를 확인할 수 없습니다'
        cwd = str(meta.get('cwd') or self.cwd)
        total_tokens = self.tokens[0] + self.tokens[2] if self.tokens else meta.get('tokens_used')
        if isinstance(total_tokens, bool) or not isinstance(total_tokens, int) or total_tokens < 0:
            total_tokens = None
        return dict(id=self.id, title=clean_title(meta.get('name') or self.title or self.id[:8]),
                    cwd=cwd, project=cwd.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1] or '프로젝트 없음',
                    model=self.model or meta.get('model') or '모델 정보 없음',
                    originator=meta.get('originator') or self.originator or 'Local',
                    parent_id=self.parent_id, branch=meta.get('git_branch') or self.branch,
                    archived=bool(meta.get('archived')), state=state, activity=activity,
                    last_at=self.last_at or meta.get('updated_at') or 0,
                    started_at=self.started_at, ended_at=self.ended_at,
                    input_tokens=self.tokens[0] if self.tokens else None,
                    cached_input_tokens=self.tokens[1] if self.tokens else None,
                    output_tokens=self.tokens[2] if self.tokens else None,
                    total_tokens=total_tokens,
                    partial=self.partial, events=list(reversed(self.events)))


class SessionReader:
    """Bounded startup tails, append cursors, partial lines, and file replacement."""
    def __init__(self, home, config=None):
        self.home = home
        self.files = {}
        self.sessions = {}
        self.config = config or read_config()
        self.attribution = Attribution(home, self.config['attribution_window_seconds'])
        self.detector = Detector(self.config)
        self.limits = {}
        self.errors = 0
        self.samples = []
        self.models = {}

    def consume(self, path, line, now):
        # Ignore message bodies, reasoning content, tool arguments and outputs.
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                return
            session = self.files[path]['session']
            at = timestamp(event.get('timestamp'))
            if at > now + 5 or at < session.born:
                return
            session.observe(event)
            if event.get('type') in ('turn_context', 'event_msg'):
                self.attribution.observe(path, event, now)
            for sample in parse_event(event):
                previous = self.limits.get(sample.key)
                if previous is None or sample.at > previous.at:
                    self.limits[sample.key] = sample
                if now - self.config['history_seconds'] <= sample.at <= now + 5:
                    self.samples.append(sample)
        except (ValueError, TypeError, AttributeError, OverflowError):
            self.errors += 1

    def open_file(self, path, stat):
        with path.open('rb') as stream:
            first = stream.readline(MAX_LINE + 1)
            if len(first) > MAX_LINE or not first.endswith(b'\n'):
                return  # Retry a metadata record that has not finished writing.
            event = json.loads(first)
            meta = event.get('payload') or {}
            if event.get('type') != 'session_meta' or not isinstance(meta, dict):
                return
            key = str(meta.get('id') or meta.get('session_id') or path.stem)
            session = Session(key, cwd=str(meta.get('cwd') or ''),
                              originator=str(meta.get('originator') or ''),
                              parent_id=meta.get('parent_thread_id'),
                              branch=str((meta.get('git') or {}).get('branch') or ''))
            # Inherited fork events belong to the parent, not the new chat.
            if meta.get('forked_from_id'):
                session.born = timestamp(meta.get('timestamp') or event.get('timestamp'))
            offset = stream.tell()
            skip = stat.st_size - offset > TAIL_BYTES
            if skip:
                offset = stat.st_size - TAIL_BYTES
                session.partial = True
            self.files[path] = dict(session=session, offset=offset, pending=b'', skip=skip,
                                    identity=(stat.st_dev, stat.st_ino))
            self.sessions[key] = session
            self.attribution.register(path, meta)
            # Startup tails may start after the turn_context. Use the locally
            # recorded current model until an explicit context record arrives.
            if self.models.get(key):
                self.attribution.logs[path]['model'] = self.models[key]

    def scan(self, now, discover=True):
        if discover:
            paths = list((self.home / 'sessions').rglob('rollout-*.jsonl'))
            existing = set(paths)
            for path in set(self.files) - existing:
                session = self.files.pop(path)['session']
                if self.sessions.get(session.id) is session:
                    self.sessions.pop(session.id, None)
                self.attribution.logs.pop(path, None)
        else:
            paths = list(self.files)
        for path in paths:
            try:
                stat = path.stat()
                cursor = self.files.get(path)
                if cursor and (cursor['offset'] > stat.st_size or
                               cursor['identity'] != (stat.st_dev, stat.st_ino)):
                    self.files.pop(path)
                    cursor = None
                if cursor is None:
                    self.open_file(path, stat)
                    cursor = self.files.get(path)
                if not cursor or cursor['offset'] == stat.st_size:
                    continue
                with path.open('rb') as stream:
                    stream.seek(cursor['offset'])
                    data = stream.read(READ_BYTES)
                    cursor['offset'] = stream.tell()
                data = cursor['pending'] + data
                lines = data.split(b'\n')
                cursor['pending'] = lines.pop()
                for line in lines:
                    if cursor['skip']:
                        cursor['skip'] = False
                    elif len(line) <= MAX_LINE:
                        self.consume(path, line, now)
                    else:
                        cursor['session'].partial = True
                if len(cursor['pending']) > MAX_LINE:
                    cursor['pending'] = b''
                    cursor['skip'] = True
                    cursor['session'].partial = True
            except (OSError, ValueError, TypeError, AttributeError):
                self.errors += 1
        # Different chats can contain interleaved quota snapshots. Replay them
        # in time order so the detector can recover intervals at startup.
        for sample in sorted(self.samples, key=lambda value: value.at):
            self.detector.observe(sample)
        self.samples.clear()
        self.detector.current(now)
        self.attribution.prune(now)

    def quota(self, now):
        rows = []
        for key, sample in sorted(self.limits.items()):
            track = self.detector.tracks.get(key)
            rows.append(dict(key=key, limit_id=sample.limit_id, window=sample.window,
                             minutes=sample.minutes, used=sample.used, remaining=100 - sample.used,
                             resets_at=sample.reset, observed_at=sample.at,
                             state=track.kind if track else 'observing',
                             stale=now - sample.at >= self.config['stale_seconds'],
                             expired=now >= sample.reset,
                             fast=bool(track and track.active),
                             seconds_per_percent=(self.detector.signal(key, track, now)['seconds_per_percent']
                                                  if track and track.active else None)))
        return rows


def attention_requests(root, limit=6):
    result = []
    for path in sorted((root / 'logs').glob('*.json'), reverse=True):
        if path.name.endswith('.status.json'):
            continue
        row = read_json(path)
        if 'message' not in row:
            continue
        result.append(dict(title=clean_title(row.get('chat_title') or row.get('title') or 'Attention'),
                           message=str(row['message'])[:300], color=row.get('color', 'cyan'),
                           at=path.stat().st_mtime))
        if len(result) >= limit:
            break
    return result


class Dashboard:
    def __init__(self, home, root=ROOT, stale_seconds=180):
        self.home, self.root = home, root
        self.stale_seconds = stale_seconds
        self.reader = SessionReader(home)
        self.metadata, self.warning = {}, None
        self.attention = []
        self.next_discovery = 0
        self.condition = threading.Condition()
        self.version = 0
        self.signature = None
        self.data = {}
        self.stopped = threading.Event()

    def scan(self, now):
        discover = now >= self.next_discovery
        if discover:
            self.metadata, self.warning = catalog(self.home)
            self.reader.models = {key: row['model'] for key, row in self.metadata.items() if row.get('model')}
            self.attention = attention_requests(self.root)
            self.next_discovery = now + 5
        self.reader.scan(now, discover)
        rows = []
        for key in self.metadata.keys() | self.reader.sessions.keys():
            session = self.reader.sessions.get(key) or Session(key)
            rows.append(session.snapshot(self.metadata.get(key, {}), now, self.stale_seconds))
        rows.sort(key=lambda row: (-row['last_at'], row['id']))
        attribution = self.reader.attribution.snapshot(now)
        for row in attribution['rows']:
            row['title'] = clean_title(self.metadata.get(row['id'], {}).get('name') or row['title'])
        # Attribution's wall clock timestamp does not constitute a content change.
        attribution.pop('updated_at', None)
        watcher = read_json(self.root / 'usage-watch' / 'status.json')
        watcher_at = watcher.get('updated_at')
        watcher_fresh = (isinstance(watcher_at, (int, float)) and now - watcher_at < 90)
        data = dict(chats=rows, quota=self.reader.quota(now), attribution=attribution,
                    attention=self.attention, source=dict(home=str(self.home),
                    files=len(self.reader.files), available=(self.home / 'sessions').is_dir(),
                    stale_seconds=self.stale_seconds, warning=self.warning,
                    fast_seconds_per_percent=self.reader.config['fast_seconds_per_percent'],
                    release_seconds_per_percent=self.reader.config['release_seconds_per_percent'],
                    skipped_records=self.reader.errors, startup_tail_bytes=TAIL_BYTES,
                    catalog_limit=500), watcher=dict(state=watcher.get('state', 'not_started'),
                    fresh=bool(watcher_fresh), edge_active=bool(watcher_fresh and
                    isinstance(watcher.get('edge'), dict) and watcher['edge'].get('active'))))
        signature = json.dumps(data, sort_keys=True, ensure_ascii=False)
        with self.condition:
            if signature != self.signature:
                self.signature = signature
                self.version += 1
                self.data = data | dict(version=self.version, updated_at=now)
                self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return self.data | dict(checked_at=time.time())

    def watch(self, interval):
        while not self.stopped.wait(interval):
            try:
                self.scan(time.time())
            except (OSError, ValueError, sqlite3.Error):
                # Keep the last complete snapshot and expose a source error.
                with self.condition:
                    self.version += 1
                    self.data = self.data | dict(version=self.version,
                              error='로컬 기록을 읽지 못했습니다. 다시 시도 중입니다.')
                    self.signature = None
                    self.condition.notify_all()


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, dashboard):
        super().__init__(address, Handler)
        self.dashboard = dashboard


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def headers_for(self, status, content_type, length=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; "
                         "style-src 'self'; connect-src 'self'; img-src 'self' data:; "
                         "base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        if length is not None:
            self.send_header('Content-Length', str(length))
        self.end_headers()

    def send_bytes(self, status, data, content_type):
        self.headers_for(status, content_type, len(data))
        self.wfile.write(data)

    def allowed(self):
        port = self.server.server_port
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        origin = self.headers.get('Origin')
        return (self.headers.get('Host') in hosts and
                (not origin or origin in {f'http://{host}' for host in hosts}) and
                self.headers.get('Sec-Fetch-Site') not in ('cross-site',))

    def do_GET(self):
        if not self.allowed():
            self.send_bytes(403, b'Local dashboard access only.', 'text/plain; charset=utf-8')
            return
        route = urlsplit(self.path).path
        if route == '/api/snapshot':
            data = json.dumps(self.server.dashboard.snapshot(), ensure_ascii=False).encode()
            self.send_bytes(200, data, 'application/json; charset=utf-8')
        elif route == '/api/events':
            self.events()
        else:
            assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                      '/dashboard.css': ('dashboard.css', 'text/css; charset=utf-8'),
                      '/dashboard.js': ('dashboard.js', 'text/javascript; charset=utf-8'),
                      '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
            if route not in assets:
                self.send_bytes(404, b'Not found', 'text/plain; charset=utf-8')
                return
            filename, content_type = assets[route]
            self.send_bytes(200, (WEB / filename).read_bytes(), content_type)

    def events(self):
        dashboard = self.server.dashboard
        self.headers_for(200, 'text/event-stream; charset=utf-8')
        self.connection.settimeout(20)
        version = -1
        try:
            while not dashboard.stopped.is_set():
                with dashboard.condition:
                    dashboard.condition.wait_for(
                        lambda: dashboard.version != version or dashboard.stopped.is_set(), timeout=10)
                    changed = dashboard.version != version
                    version = dashboard.version
                    data = dashboard.snapshot() if changed else dict(checked_at=time.time())
                event = 'snapshot' if changed else 'heartbeat'
                payload = json.dumps(data, ensure_ascii=False)
                self.wfile.write(f'event: {event}\ndata: {payload}\n\n'.encode())
                self.wfile.flush()
        except (OSError, TimeoutError):
            pass
        finally:
            self.close_connection = True


def state_paths(port):
    STATE.mkdir(exist_ok=True)
    return STATE / f'{port}.json', STATE / f'{port}.stop', STATE / f'{port}.lock'


def take_lock(path):
    import msvcrt
    stream = path.open('a+b')
    if stream.tell() == 0:
        stream.write(b'0')
        stream.flush()
    stream.seek(0)
    try:
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        return stream
    except OSError:
        stream.close()
        return None


def launch(args):
    command = [sys.executable, str(Path(__file__).resolve()), 'run', '--port', str(args.port),
               '--home', str(args.home), '--poll-seconds', str(args.poll_seconds),
               '--stale-seconds', str(args.stale_seconds)]
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    script = (
        "$ErrorActionPreference='Stop'; "
        "$startup=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly "
        "-Property @{ShowWindow=[uint16]0;CreateFlags=[uint32]0x09000000}; "
        "$result=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{"
        f"CommandLine={quote(subprocess.list2cmdline(command))};CurrentDirectory={quote(ROOT)};"
        "ProcessStartupInformation=$startup}; "
        "if($result.ReturnValue -ne 0){throw ('Process creation failed: '+$result.ReturnValue)}"
    )
    shell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-Command', script],
                   capture_output=True, text=True, check=True, timeout=20,
                   creationflags=subprocess.CREATE_NO_WINDOW)


def run(args, lock):
    status, stop, _ = state_paths(args.port)
    dashboard = Dashboard(args.home, stale_seconds=args.stale_seconds)
    server = None
    started = time.time()
    try:
        # Bind before reading records so an occupied port fails immediately.
        server = DashboardServer(('127.0.0.1', args.port), dashboard)
        server.timeout = .5
        dashboard.scan(time.time())
        thread = threading.Thread(target=dashboard.watch, args=(args.poll_seconds,), daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{args.port}'
        status.write_text(json.dumps(dict(pid=os.getpid(), url=url, started_at=started,
                                          state='running')), 'utf-8')
        print(f'Codex Attention: {url}', flush=True)
        if args.open:
            webbrowser.open(url)
        while not (stop.exists() and stop.stat().st_mtime >= started):
            server.handle_request()
    finally:
        dashboard.stopped.set()
        with dashboard.condition:
            dashboard.condition.notify_all()
        if server:
            server.server_close()
        status.write_text(json.dumps(dict(state='stopped', updated_at=time.time())), 'utf-8')
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='start', choices=('start', 'run', 'stop', 'status'))
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--home', type=lambda p: Path(p).expanduser().resolve(),
                        default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).resolve())
    parser.add_argument('--poll-seconds', type=float, default=1)
    parser.add_argument('--stale-seconds', type=float, default=180)
    parser.add_argument('--open', action='store_true', help='Open the local dashboard in the default browser')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be 1..65535')
    if not math.isfinite(args.poll_seconds) or args.poll_seconds < .5:
        parser.error('--poll-seconds must be at least 0.5')
    if not math.isfinite(args.stale_seconds) or args.stale_seconds < 10:
        parser.error('--stale-seconds must be at least 10')
    status, stop, lock_path = state_paths(args.port)
    lock = take_lock(lock_path)
    running = lock is None
    if args.action == 'status':
        if lock:
            lock.close()
        print(json.dumps(read_json(status) | dict(running=running), ensure_ascii=False))
        return
    if args.action == 'stop':
        if lock:
            lock.close()
        if running:
            stop.touch()
        print('Stop requested.' if running else 'Dashboard is already stopped.')
        return
    if running:
        print(f'Dashboard already running: http://127.0.0.1:{args.port}')
        if args.open:
            webbrowser.open(f'http://127.0.0.1:{args.port}')
        return
    if args.action == 'run':
        try:
            run(args, lock)
        except KeyboardInterrupt:
            pass
    else:
        lock.close()
        launched_at = time.time()
        launch(args)
        for _ in range(100):
            current = read_json(status)
            if current.get('state') == 'running' and current.get('started_at', 0) >= launched_at:
                check = take_lock(lock_path)
                if check is None:
                    print(f"Codex Attention: {current['url']}")
                    if args.open:
                        webbrowser.open(current['url'])
                    return
                check.close()
            time.sleep(.1)
        parser.exit(1, 'Dashboard did not start. Run with "run" to inspect the error; check the port.\n')


if __name__ == '__main__':
    main()
