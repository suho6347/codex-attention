from datetime import datetime, timezone
from contextlib import closing
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest

from codex_dashboard import (Dashboard, DashboardServer, MAX_LINE, Session,
                             SessionReader, catalog)


def event(at, kind, **payload):
    return dict(timestamp=datetime.fromtimestamp(at, timezone.utc).isoformat(),
                type=kind, payload=payload)


def record(at, subtype, **payload):
    return event(at, 'event_msg', type=subtype, **payload)


def encoded(value):
    return json.dumps(value).encode() + b'\n'


class SessionTests(unittest.TestCase):
    def test_cumulative_tokens_do_not_double_count_cache_reasoning_or_repeated_snapshots(self):
        session = Session('chat')
        usage = dict(input_tokens=1000, cached_input_tokens=800,
                     output_tokens=200, reasoning_output_tokens=150, total_tokens=1200)
        for at in (100, 101):
            session.observe(record(at, 'token_count', info={'total_token_usage': usage}))
        self.assertEqual(session.snapshot({'tokens_used': 900}, 102, 180)['total_tokens'], 1200)

    def test_cumulative_tokens_fall_back_to_metadata_without_inventing_missing_values(self):
        session = Session('chat')
        for value in (0, 1234567):
            self.assertEqual(session.snapshot({'tokens_used': value}, 100, 180)['total_tokens'], value)
        for value in (None, -1, True, '123', 1.5):
            self.assertIsNone(session.snapshot({'tokens_used': value}, 100, 180)['total_tokens'])

    def test_lifecycle_and_unrelated_completion(self):
        session = Session('chat')
        session.observe(record(100, 'task_started', turn_id='new'))
        session.observe(event(101, 'response_item', type='reasoning'))
        session.observe(record(102, 'task_complete', turn_id='old'))
        self.assertEqual(session.state, 'running')
        session.observe(record(103, 'task_complete', turn_id='new'))
        self.assertEqual(session.state, 'completed')
        self.assertEqual(session.ended_at, 103)
        session.observe(record(104, 'task_started', turn_id='next'))
        self.assertEqual(session.state, 'running')
        self.assertEqual(session.ended_at, 0)

    def test_user_input_and_tool_output(self):
        session = Session('chat')
        session.observe(record(100, 'task_started'))
        session.observe(event(101, 'response_item', type='function_call',
                              name='functions.request_user_input', call_id='q'))
        self.assertEqual(session.state, 'waiting')
        session.observe(event(102, 'response_item', type='custom_tool_call_output', call_id='q'))
        self.assertEqual(session.state, 'running')
        session.observe(record(103, 'turn_aborted'))
        self.assertEqual(session.state, 'interrupted')

    def test_waiting_survives_unrelated_tool_result(self):
        session = Session('chat')
        session.observe(event(100, 'response_item', type='function_call',
                              name='request_user_input', call_id='q'))
        session.observe(event(101, 'response_item', type='function_call_output', call_id='other'))
        self.assertEqual(session.state, 'waiting')

    def test_error_completion_and_final_answer(self):
        session = Session('chat')
        session.observe(record(100, 'task_complete', error={'message': 'private'}))
        self.assertEqual(session.state, 'error')
        session.observe(event(101, 'response_item', type='message', role='assistant',
                              phase='final_answer', content=[{'text': 'private answer'}]))
        self.assertEqual(session.state, 'completed')
        self.assertNotIn('private', json.dumps(session.snapshot({}, 102, 180)))

    def test_silent_running_chat_becomes_unknown_not_completed(self):
        session = Session('chat')
        session.observe(record(100, 'task_started'))
        self.assertEqual(session.snapshot({}, 279, 180)['state'], 'running')
        self.assertEqual(session.snapshot({}, 280, 180)['state'], 'stale')
        session.observe(event(281, 'response_item', type='reasoning'))
        self.assertEqual(session.snapshot({}, 282, 180)['state'], 'running')

    def test_old_and_invalid_records_do_not_change_state(self):
        session = Session('chat')
        session.observe(record(200, 'task_complete'))
        session.observe(record(100, 'task_started'))
        session.observe({'timestamp': 'bad', 'payload': {}})
        session.observe({'timestamp': '2026-01-01T00:00:00Z', 'payload': []})
        self.assertEqual(session.state, 'completed')


class LocalDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / 'sessions').mkdir()
        self.path = self.home / 'sessions' / 'rollout-test.jsonl'
        self.path.write_bytes(encoded(event(90, 'session_meta', id='chat',
                                            originator='Codex Desktop', cwd='F:/demo')))
        self.reader = SessionReader(self.home)

    def append(self, *events):
        with self.path.open('ab') as stream:
            for value in events:
                stream.write(encoded(value))

    def test_partial_appends_and_no_duplicate_events(self):
        self.append(record(100, 'task_started'))
        self.reader.scan(101)
        data = encoded(record(102, 'task_complete'))
        with self.path.open('ab') as stream:
            stream.write(data[:35])
        self.reader.scan(103, False)
        self.assertEqual(self.reader.sessions['chat'].state, 'running')
        with self.path.open('ab') as stream:
            stream.write(data[35:])
        self.reader.scan(104, False)
        session = self.reader.sessions['chat']
        self.assertEqual(session.state, 'completed')
        count = len(session.events)
        self.reader.scan(105, False)
        self.assertEqual(len(session.events), count)

    def test_truncated_file_resets_cursor(self):
        self.append(record(100, 'task_started'), record(101, 'task_complete'))
        self.reader.scan(102)
        self.path.write_bytes(encoded(event(103, 'session_meta', id='chat')) +
                              encoded(record(104, 'task_started')))
        self.reader.scan(105, False)
        self.assertEqual(self.reader.sessions['chat'].state, 'running')
        self.assertEqual(self.reader.sessions['chat'].started_at, 104)

    def test_partial_metadata_is_retried(self):
        data = encoded(event(90, 'session_meta', id='chat'))
        self.path.write_bytes(data[:20])
        self.reader.scan(100)
        self.assertEqual(self.reader.files, {})
        self.path.write_bytes(data + encoded(record(100, 'task_started')))
        self.reader.scan(101)
        self.assertEqual(self.reader.sessions['chat'].state, 'running')

    def test_large_tool_output_is_bounded_and_next_status_is_found(self):
        self.append(record(100, 'task_started'))
        self.reader.scan(101)
        # Read an incomplete overlong line over multiple scan cycles.
        with self.path.open('ab') as stream:
            stream.write(b'{"private_tool_output":"' + b'x' * (MAX_LINE + 100))
        self.reader.scan(102, False)
        self.assertLessEqual(len(self.reader.files[self.path]['pending']), MAX_LINE)
        with self.path.open('ab') as stream:
            stream.write(b'"}\n' + encoded(record(103, 'task_complete')))
        self.reader.scan(104, False)
        self.assertEqual(self.reader.sessions['chat'].state, 'completed')
        self.assertTrue(self.reader.sessions['chat'].partial)

    def test_startup_tail_recovers_terminal_state(self):
        self.append(record(100, 'task_started'))
        with self.path.open('ab') as stream:
            stream.write(encoded(event(101, 'response_item', type='function_call_output',
                                       output='x' * (MAX_LINE + 100))))
        self.append(record(102, 'task_complete'))
        self.reader.scan(103)
        self.assertEqual(self.reader.sessions['chat'].state, 'completed')
        self.assertTrue(self.reader.sessions['chat'].partial)

    def test_inherited_fork_and_future_events_are_ignored(self):
        self.path.write_bytes(encoded(event(200, 'session_meta', id='fork',
                              forked_from_id='parent', timestamp='1970-01-01T00:03:20Z')))
        self.append(record(100, 'task_started'), record(201, 'task_complete'),
                    record(9999, 'task_started'))
        self.reader.scan(202)
        self.assertEqual(self.reader.sessions['fork'].state, 'completed')
        self.assertEqual(self.reader.sessions['fork'].started_at, 0)

    def test_deleted_files_are_removed(self):
        self.reader.scan(101)
        self.path.unlink()
        self.reader.scan(102)
        self.assertEqual(self.reader.files, {})
        self.assertEqual(self.reader.sessions, {})

    def test_catalog_uses_name_and_never_exposes_prompts(self):
        database = self.home / 'state_5.sqlite'
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('CREATE TABLE threads (id TEXT, name TEXT, title TEXT, updated_at INTEGER, tokens_used INTEGER)')
            connection.execute('INSERT INTO threads VALUES (?,?,?,?,?)',
                               ('chat', 'Renamed title', 'private prompt', 100, 1234567))
            connection.commit()
        values, warning = catalog(self.home)
        self.assertIsNone(warning)
        self.assertEqual(values['chat']['name'], 'Renamed title')
        self.assertEqual(values['chat']['tokens_used'], 1234567)
        self.assertNotIn('private prompt', json.dumps(values))
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute('SELECT title FROM threads').fetchone()[0], 'private prompt')

    def test_dashboard_does_not_leak_message_body_or_tool_arguments(self):
        self.append(record(100, 'task_started'),
                    event(101, 'response_item', type='message', role='assistant',
                          content=[{'text': 'SECRET_MESSAGE'}]),
                    event(102, 'response_item', type='function_call', name='exec',
                          arguments='SECRET_ARGUMENTS', call_id='one'),
                    event(103, 'response_item', type='function_call_output', output='SECRET_OUTPUT'))
        dashboard = Dashboard(self.home, root=self.home)
        dashboard.scan(104)
        data = json.dumps(dashboard.snapshot())
        self.assertNotIn('SECRET_', data)
        self.assertEqual(dashboard.snapshot()['chats'][0]['state'], 'running')

    def test_metadata_model_seeds_usage_when_context_is_outside_tail(self):
        self.reader.models['chat'] = 'gpt-6.1-sol'
        self.append(record(100, 'token_count', info={
            'total_token_usage': {'input_tokens': 100, 'output_tokens': 10},
            'last_token_usage': {'input_tokens': 100, 'output_tokens': 10}}))
        self.reader.scan(101)
        snapshot = self.reader.attribution.snapshot(101)
        self.assertEqual(snapshot['basis'], 'credit_proxy')
        self.assertEqual(snapshot['rows'][0]['models'], 'gpt-6.1-sol')

    def test_quota_replay_is_sorted_across_chats_and_expires(self):
        def quota(at, used):
            return record(at, 'token_count', rate_limits={'primary': {
                'used_percent': used, 'window_minutes': 300, 'resets_at': 5000}})
        self.append(quota(100, 10), quota(300, 12))
        other = self.home / 'sessions' / 'rollout-other.jsonl'
        other.write_bytes(encoded(event(90, 'session_meta', id='other')) + encoded(quota(200, 11)))
        self.reader.scan(301)
        self.assertTrue(self.reader.quota(301)[0]['fast'])
        self.reader.scan(500, False)
        self.assertTrue(self.reader.quota(500)[0]['stale'])
        self.assertFalse(self.reader.quota(500)[0]['fast'])


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dashboard = Dashboard(Path(self.temp.name), root=Path(self.temp.name))
        self.dashboard.scan(time.time())
        self.server = DashboardServer(('127.0.0.1', 0), self.dashboard)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.dashboard.stopped.set()
        with self.dashboard.condition:
            self.dashboard.condition.notify_all()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        self.addCleanup(connection.close)
        connection.request('GET', path, headers=headers or {})
        return connection.getresponse()

    def test_static_and_snapshot(self):
        response = self.request('/')
        self.assertEqual(response.status, 200)
        self.assertIn(b'<!doctype html>', response.read())
        self.assertIn("frame-ancestors 'none'", response.getheader('Content-Security-Policy'))
        response = self.request('/api/snapshot')
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())['chats'], [])
        self.assertEqual(response.getheader('Cache-Control'), 'no-store')

    def test_cross_origin_and_dns_rebinding_are_denied(self):
        for headers in ({'Origin': 'https://example.com'}, {'Host': 'evil.test'},
                        {'Sec-Fetch-Site': 'cross-site'}, {'Origin': 'null'}):
            response = self.request('/api/snapshot', headers)
            self.assertEqual(response.status, 403)
            response.read()

    def test_only_allowlisted_assets_can_be_read(self):
        for path in ('/../codex_dashboard.py', '/auth.json', '/%2e%2e/auth.json', '/api/stop'):
            response = self.request(path)
            self.assertEqual(response.status, 404)
            response.read()

    def test_event_stream_pushes_new_snapshots(self):
        response = self.request('/api/events')
        self.assertEqual(response.getheader('Content-Type'), 'text/event-stream; charset=utf-8')
        self.assertEqual(response.readline(), b'event: snapshot\n')
        initial = json.loads(response.readline().decode().removeprefix('data: '))
        response.readline()
        with self.dashboard.condition:
            self.dashboard.version += 1
            self.dashboard.data = self.dashboard.data | dict(version=self.dashboard.version, marker='changed')
            self.dashboard.condition.notify_all()
        self.assertEqual(response.readline(), b'event: snapshot\n')
        changed = json.loads(response.readline().decode().removeprefix('data: '))
        self.assertGreater(changed['version'], initial['version'])
        self.assertEqual(changed['marker'], 'changed')


if __name__ == '__main__':
    unittest.main()
