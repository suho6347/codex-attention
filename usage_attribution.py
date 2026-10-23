"""Bounded, local-only estimates of which chats have recent token activity."""
from collections import deque
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3

# Standard ChatGPT credit rates per million tokens, checked 2026-10-23.
# https://learn.chatgpt.com/docs/pricing (not API dollar prices).
# This is a comparison proxy, NOT the subscription quota allocation formula.
CREDIT_RATES = {
    'gpt-6-astra': (250, 25, 1250),
    'gpt-6.1-sol': (50, 2.5, 250),
    'gpt-6-sol': (50, 5, 250),
    'gpt-6-luna': (2.5, .25, 12.5),
    'gpt-5.6-sol': (100, 10, 500),
    'gpt-5.6-terra': (50, 5, 300),
    'gpt-5.6-luna': (5, .5, 30),
}


def usage_vector(value):
    if not isinstance(value, dict):
        return None
    result = tuple(value.get(k, 0) for k in
                   ('input_tokens', 'cached_input_tokens', 'output_tokens'))
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in result):
        return None
    return result[0], min(result[0], result[1]), result[2]


def clean_title(value):
    return ' '.join(str(value).split())[:90]


class TitleCache:
    """Read only requested titles, only while an alert is visible."""
    def __init__(self, home):
        self.home, self.values, self.expires = Path(home).expanduser().resolve(), {}, 0

    def get(self, ids, now):
        if now >= self.expires or any(i not in self.values for i in ids):
            self.expires = now + 30
            self.values = {i: i[:8] for i in ids}
            databases = sorted(self.home.glob('state_*.sqlite'),
                               key=lambda p: int(p.stem.split('_')[-1])
                               if p.stem.split('_')[-1].isdigit() else -1,
                               reverse=True)
            if databases:
                try:
                    with closing(sqlite3.connect(databases[0].as_uri() + '?mode=ro',
                                                 uri=True, timeout=.05)) as connection:
                        marks = ','.join('?' for _ in ids)
                        columns = {row[1] for row in connection.execute('PRAGMA table_info(threads)')}
                        # In the desktop database title is the initial prompt;
                        # name is the current sidebar title, including renames.
                        title_column = "COALESCE(NULLIF(name, ''), title)" if 'name' in columns else 'title'
                        for key, title in connection.execute(
                                f'SELECT id, {title_column} FROM threads WHERE id IN ({marks})', list(ids)):
                            if title:
                                self.values[key] = clean_title(title)
                    return self.values
                except sqlite3.Error:
                    pass
            try:
                with (self.home / 'session_index.jsonl').open(encoding='utf-8') as stream:
                    for line in stream:
                        try:
                            row = json.loads(line)
                            if row.get('id') in self.values and row.get('thread_name'):
                                self.values[row['id']] = clean_title(row['thread_name'])
                        except (ValueError, AttributeError):
                            continue
            except OSError:
                pass
        return self.values


class Attribution:
    def __init__(self, home, window_seconds=300, max_events=20000):
        self.window_seconds, self.max_events = window_seconds, max_events
        self.logs, self.parents, self.events = {}, {}, deque()
        self.seen = {}
        self.titles = TitleCache(home)
        self.truncated_until = 0

    def register(self, path, meta):
        # id is the actual chat owning this rollout; session_id can name an
        # inherited session in a fork. Never group separate forks together.
        key = str(meta.get('id') or meta.get('session_id') or Path(path).stem)
        born = 0
        if meta.get('forked_from_id'):
            try:
                born = datetime.fromisoformat(meta['timestamp'].replace('Z', '+00:00')).timestamp()
            except (KeyError, ValueError, AttributeError):
                pass
        self.logs[path] = dict(id=key, model='unknown', total=None, at=0, born=born)
        if meta.get('parent_thread_id'):
            self.parents[key] = str(meta['parent_thread_id'])

    def root(self, key):
        seen = set()
        while key in self.parents and key not in seen:
            seen.add(key)
            key = self.parents[key]
        return key

    def observe(self, path, event, now):
        state = self.logs.get(path)
        if state is None:
            return
        payload = event.get('payload') or {}
        if event.get('type') == 'turn_context':
            if isinstance(payload.get('model'), str):
                state['model'] = payload['model']
            return
        if event.get('type') != 'event_msg' or payload.get('type') != 'token_count':
            return
        info = payload.get('info') or {}
        total = usage_vector(info.get('total_token_usage'))
        if total is None:
            return
        try:
            at = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00')).timestamp()
        except (ValueError, KeyError, AttributeError):
            return
        if at > now + 5 or at <= state['at']:
            return
        previous = state['total']
        state['total'], state['at'] = total, at
        if previous == total:
            return  # Cached quota updates repeat the same token snapshot.
        if previous is None or total[0] < previous[0] or total[2] < previous[2]:
            # New logs and compaction/counter resets: count at most the last
            # response, never an inherited lifetime cumulative total.
            delta = usage_vector(info.get('last_token_usage'))
        else:
            delta = tuple(max(0, n - old) for n, old in zip(total, previous))
        if delta is None or at < max(state['born'], now - self.window_seconds):
            return
        tokens = delta[0] + delta[2]  # reasoning is already in output_tokens.
        if not tokens:
            return
        fingerprint = (state['id'], at, total)
        if fingerprint in self.seen:
            return
        self.seen[fingerprint] = at
        if len(self.seen) > self.max_events:
            self.seen.pop(next(iter(self.seen)))
        cached = min(delta[0], delta[1])
        model = state['model']
        rates = CREDIT_RATES.get(model)
        score = ((delta[0] - cached) * rates[0] + cached * rates[1] + delta[2] * rates[2]) if rates else None
        self.events.append((at, state['id'], model, tokens, score))
        if len(self.events) > self.max_events:
            removed = self.events.popleft()
            self.truncated_until = max(self.truncated_until, removed[0] + self.window_seconds)

    def prune(self, now):
        # Files can be scanned in any order. Filter once per existing scan,
        # rather than scheduling another timer for attribution.
        cutoff = now - self.window_seconds
        self.events = deque(e for e in self.events if e[0] >= cutoff)
        self.seen = {k: at for k, at in self.seen.items() if at >= cutoff}

    def snapshot(self, now, top=5):
        self.prune(now)
        weighted = all(e[4] is not None for e in self.events)
        groups = {}
        for at, key, model, tokens, score in self.events:
            key = self.root(key)
            group = groups.setdefault(key, dict(id=key, score=0, models=set(), last_at=0))
            group['score'] += score if weighted else tokens
            group['models'].add(model)
            group['last_at'] = max(group['last_at'], at)
        total = sum(g['score'] for g in groups.values())
        ranked = sorted(groups.values(), key=lambda g: (-g['score'], g['id']))
        titles = self.titles.get([g['id'] for g in ranked[:top]], now) if ranked else {}
        rows = [dict(id=g['id'], title=titles.get(g['id'], g['id'][:8]),
                     percent=round(100 * g['score'] / total, 1),
                     models=', '.join(sorted(g['models'])), last_at=g['last_at'])
                for g in ranked[:top]] if total else []
        return dict(window_seconds=self.window_seconds, basis='credit_proxy' if weighted else 'tokens',
                    rows=rows, other_percent=round(sum(100 * g['score'] / total for g in ranked[top:]), 1) if total else 0,
                    chat_count=len(ranked), updated_at=now,
                    partial=now < self.truncated_until,
                    note='Local observed chats only. Standard credit-rate proxy; not actual quota allocation. Speed mode and other devices are not measured.')
