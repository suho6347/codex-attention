from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from codex_usage_watch import Reader
from usage_attribution import Attribution, TitleCache


def event(at, inputs, cached=0, output=0, last=None):
    total = dict(input_tokens=inputs, cached_input_tokens=cached, output_tokens=output)
    return dict(type='event_msg', timestamp=datetime.fromtimestamp(at, timezone.utc).isoformat(),
                payload=dict(type='token_count', info=dict(total_token_usage=total,
                                                          last_token_usage=last or total)))


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.usage = Attribution(self.home)

    def register(self, path, model='gpt-6.1-sol', **meta):
        self.usage.register(path, dict(id=path, **meta))
        self.usage.observe(path, dict(type='turn_context', payload=dict(model=model)), 1000)

    def test_model_and_cache_rates_change_the_ranking(self):
        self.register('sol')
        self.register('luna', 'gpt-6-luna')
        self.usage.observe('sol', event(990, 1000, 500, 100), 1000)
        self.usage.observe('luna', event(990, 1000, 500, 100), 1000)
        rows = self.usage.snapshot(1000)['rows']
        self.assertEqual(rows[0]['id'], 'sol')
        # Sol: 51250, Luna: 2625, using credit rates, not raw tokens.
        self.assertAlmostEqual(rows[0]['percent'], 95.1)
        self.assertAlmostEqual(sum(r['percent'] for r in rows), 100)

    def test_first_snapshot_counts_last_response_not_lifetime(self):
        self.register('chat')
        self.usage.observe('chat', event(990, 9000000, last=dict(input_tokens=100)), 1000)
        self.assertEqual(self.usage.events[0][3], 100)

    def test_repeated_quota_snapshots_and_out_of_order_are_ignored(self):
        self.register('chat')
        for e in (event(990, 100), event(991, 100), event(989, 90), event(992, 150)):
            self.usage.observe('chat', e, 1000)
        self.assertEqual(sum(e[3] for e in self.usage.events), 150)

    def test_fork_does_not_count_copied_parent_history(self):
        self.register('fork', forked_from_id='parent', timestamp='1970-01-01T00:16:30Z')
        self.usage.observe('fork', event(980, 900000), 1000)
        self.usage.observe('fork', event(995, 900100), 1000)
        self.assertEqual(sum(e[3] for e in self.usage.events), 100)
        self.assertEqual(self.usage.snapshot(1000)['rows'][0]['id'], 'fork')

    def test_subagent_activity_is_grouped_with_its_parent_chat(self):
        self.register('parent')
        self.register('child', parent_thread_id='parent')
        self.usage.observe('parent', event(990, 100), 1000)
        self.usage.observe('child', event(990, 50), 1000)
        rows = self.usage.snapshot(1000)['rows']
        self.assertEqual([(r['id'], r['percent']) for r in rows], [('parent', 100)])

    def test_counter_reset_counts_only_the_last_response(self):
        self.register('chat')
        self.usage.observe('chat', event(990, 1000), 1000)
        self.usage.observe('chat', event(991, 500, last=dict(input_tokens=20)), 1000)
        self.assertEqual(sum(e[3] for e in self.usage.events), 1020)

    def test_unknown_model_falls_back_for_the_whole_comparison(self):
        self.register('known')
        self.register('new', 'a-future-model')
        self.usage.observe('known', event(990, 100), 1000)
        self.usage.observe('new', event(990, 300), 1000)
        data = self.usage.snapshot(1000)
        self.assertEqual(data['basis'], 'tokens')
        self.assertEqual(data['rows'][0]['percent'], 75)

    def test_expiration_works_without_new_token_events(self):
        self.register('chat')
        self.usage.observe('chat', event(990, 100), 1000)
        self.assertEqual(self.usage.snapshot(1291)['rows'], [])

    def test_top_list_has_a_separate_other_share(self):
        for index in range(7):
            key = 'chat' + str(index)
            self.register(key)
            self.usage.observe(key, event(990, 100), 1000)
        data = self.usage.snapshot(1000)
        self.assertEqual(len(data['rows']), 5)
        self.assertEqual(data['other_percent'], 28.6)

    def test_storage_is_bounded_and_marked_partial(self):
        self.usage.max_events = 2
        self.register('chat')
        for at, tokens in ((990, 10), (991, 20), (992, 30)):
            self.usage.observe('chat', event(at, tokens), 1000)
        self.assertEqual(len(self.usage.events), 2)
        self.assertTrue(self.usage.snapshot(1000)['partial'])

    def test_duplicate_files_for_same_chat_do_not_double_count(self):
        self.register('chat')
        self.usage.register('second-file', dict(id='chat'))
        self.usage.observe('second-file', dict(type='turn_context', payload=dict(model='gpt-6.1-sol')), 1000)
        for path in ('chat', 'second-file'):
            self.usage.observe(path, event(990, 100), 1000)
        self.assertEqual(len(self.usage.events), 1)

    def test_titles_are_read_locally_without_changing_the_database(self):
        db = self.home / 'state_5.sqlite'
        con = sqlite3.connect(db)
        con.execute('CREATE TABLE threads (id TEXT, title TEXT)')
        con.execute('INSERT INTO threads VALUES (?, ?)', ('chat', 'Synthetic\nchat title'))
        con.commit()
        con.close()
        before = db.read_bytes()
        self.assertEqual(TitleCache(self.home).get(['chat'], 1000)['chat'], 'Synthetic chat title')
        self.assertEqual(db.read_bytes(), before)

    def test_sidebar_name_is_preferred_over_initial_message(self):
        db = self.home / 'state_5.sqlite'
        con = sqlite3.connect(db)
        con.execute('CREATE TABLE threads (id TEXT, title TEXT, name TEXT)')
        con.execute('INSERT INTO threads VALUES (?, ?, ?)', ('chat', 'An initial long question', 'Renamed chat'))
        con.commit()
        con.close()
        self.assertEqual(TitleCache(self.home).get(['chat'], 1000)['chat'], 'Renamed chat')

    def test_reader_collects_attribution_in_the_existing_incremental_read(self):
        path = self.home / 'rollout-test.jsonl'
        meta = dict(type='session_meta', payload=dict(id='chat', originator='Codex Desktop'))
        context = dict(type='turn_context', payload=dict(model='gpt-6.1-sol'))
        path.write_text('\n'.join(json.dumps(e) for e in (meta, context, event(990, 100))) + '\n')
        reader = Reader(self.home, attribution=self.usage)
        reader.scan(1000)
        self.assertEqual(sum(e[3] for e in self.usage.events), 100)
        reader.scan(1000, discover=False)
        self.assertEqual(sum(e[3] for e in self.usage.events), 100)
        with path.open('a') as stream:
            stream.write(json.dumps(event(995, 150)) + '\n')
        reader.scan(1000, discover=False)
        self.assertEqual(sum(e[3] for e in self.usage.events), 150)


if __name__ == '__main__':
    unittest.main()
