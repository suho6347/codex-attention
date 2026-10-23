import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_usage_watch import (DEFAULTS, Detector, EdgeController, Reader, Sample,
                              Wakeups, parse_line, signal_stop, stop_event_name)


def sample(t, used, reset=2000000000):
    return Sample(t, used, 'codex', 'primary', 10080, reset)


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.detector = Detector(DEFAULTS)
        self.detector.observe(sample(0, 10))

    def feed(self, at, used):
        track = self.detector.tracks['codex:primary:10080']
        for t in range(int(track.at)+30, at, 30):
            self.detector.observe(sample(t, track.used))
            self.detector.current(t)
        signal = self.detector.observe(sample(at, used))
        self.detector.current(at)
        return signal

    def normal_history(self):
        for t, used in [(1200, 11), (2400, 12), (3600, 13), (4800, 14)]:
            self.assertIsNone(self.feed(t, used))

    def accelerate(self):
        self.normal_history()
        return self.feed(5280, 15)

    def test_does_not_infer_acceleration_from_initial_integer_tick(self):
        self.assertIsNone(self.feed(5, 11))
        self.assertIsNone(self.detector.current(5))

    def test_uses_actual_completed_percentage_interval(self):
        signal = self.accelerate()
        self.assertAlmostEqual(signal['rate_pp_per_min'], .125)
        self.assertEqual(signal['seconds_per_percent'], 480)

    def test_same_fast_use_is_detected_after_different_slow_histories(self):
        for previous_interval in (900, 3600):
            with self.subTest(previous_interval=previous_interval):
                self.detector = Detector(DEFAULTS)
                self.detector.observe(sample(0, 10))
                self.assertIsNone(self.feed(previous_interval, 11))
                self.assertIsNone(self.feed(2 * previous_interval, 12))
                signal = self.feed(2 * previous_interval + 480, 13)
                self.assertAlmostEqual(signal['rate_pp_per_min'], .125)

    def test_doubling_a_slow_rate_does_not_trigger(self):
        self.feed(2400, 11)
        self.assertIsNone(self.feed(4800, 12))
        self.assertIsNone(self.feed(6000, 13))
        self.assertIsNone(self.detector.current(6000))

    def test_fast_use_does_not_need_a_learning_period(self):
        self.assertIsNone(self.feed(5, 11))
        signal = self.feed(485, 12)
        self.assertAlmostEqual(signal['rate_pp_per_min'], .125)

    def test_multi_point_drop_is_detected_immediately(self):
        signal = self.feed(5, 13)
        self.assertIsNotNone(signal)
        self.assertAlmostEqual(signal['rate_pp_per_min'], 24)

    def test_large_drop_across_missing_records_is_still_detected(self):
        self.detector.current(121)
        signal = self.detector.observe(sample(900, 14))
        self.assertIsNotNone(signal)
        self.assertAlmostEqual(signal['rate_pp_per_min'], .2)

    def test_constant_fast_use_keeps_edge_active(self):
        self.accelerate()
        for i in range(1, 10):
            self.feed(5280+480*i, 15+i)
            self.assertAlmostEqual(self.detector.current(5280+480*i)['rate_pp_per_min'], .125)

    def test_fast_state_is_still_active_after_twelve_seconds(self):
        self.accelerate()
        self.assertIsNotNone(self.detector.current(5293))

    def test_return_to_normal_clears_immediately_on_next_change(self):
        self.accelerate()
        self.feed(6180, 16)
        self.assertIsNone(self.detector.current(6180))

    def test_ongoing_flat_level_eventually_clears(self):
        self.accelerate()
        self.feed(6081, 15)
        self.assertIsNone(self.detector.current(6081))

    def test_stale_source_cannot_keep_edge_active(self):
        self.accelerate()
        self.assertIsNone(self.detector.current(5401))

    def test_timeout_moves_when_fresh_record_arrives(self):
        self.accelerate()
        self.assertEqual(self.detector.next_deadline(), 5400)
        self.feed(5370, 15)
        self.assertEqual(self.detector.next_deadline(), 5490)
        self.assertIsNotNone(self.detector.current(5400))
        self.feed(6020, 15)
        self.assertEqual(self.detector.next_deadline(), 6080)
        self.assertIsNone(self.detector.current(6080))

    def test_expired_deadline_is_removed_to_avoid_busy_wait(self):
        self.assertEqual(self.detector.next_deadline(), 120)
        self.detector.current(120)
        self.assertIsNone(self.detector.next_deadline())

    def test_gap_does_not_turn_first_fractional_tick_into_a_fast_interval(self):
        for expire_first in (False, True):
            with self.subTest(expire_first=expire_first):
                self.detector = Detector(DEFAULTS)
                self.detector.observe(sample(0, 10))
                self.normal_history()
                if expire_first:
                    self.detector.current(4921)
                self.assertIsNone(self.detector.observe(sample(10000, 15)))
                track = self.detector.tracks['codex:primary:10080']
                self.assertIsNone(track.rate)
                # The first new tick may be only a fractional percentage away.
                self.assertIsNone(self.feed(10001, 16))
                signal = self.feed(10481, 17)
                self.assertAlmostEqual(signal['rate_pp_per_min'], .125)

    def test_stale_rebounds_do_not_count_again(self):
        self.accelerate()
        self.detector.observe(sample(5290, 14))
        self.detector.observe(sample(5291, 15))
        track = self.detector.tracks['codex:primary:10080']
        self.assertEqual(track.last_change_at, 5280)
        self.assertAlmostEqual(track.rate, .125)

    def test_reset_clears_and_old_generation_is_ignored(self):
        self.accelerate()
        self.detector.observe(sample(5340, 0, 2001000000))
        self.detector.observe(sample(5341, 100, 2000000000))
        self.assertIsNone(self.detector.current(5341))
        self.assertEqual(self.detector.tracks['codex:primary:10080'].used, 0)

    def test_duplicate_and_out_of_order_records_are_ignored(self):
        self.normal_history()
        self.assertIsNone(self.detector.observe(sample(4799, 30)))
        self.assertIsNone(self.detector.observe(sample(4800, 30)))

    def test_synthetic_fast_sequence_is_detected_and_then_clears(self):
        self.detector = Detector(DEFAULTS)
        self.detector.observe(sample(0, 10))
        points = [(900,11),(2100,12),(2580,13),(3000,14),(4200,15)]
        entries = []
        for at, used in points:
            signal = self.feed(at, used)
            if signal:
                entries.append(signal['remaining'])
            if used == 14:
                self.assertIsNotNone(self.detector.current(at))
        self.assertIn(87, entries)
        self.assertIsNone(self.detector.current(points[-1][0]))


class EdgeTests(unittest.TestCase):
    def test_inactive_edge_does_not_rewrite_control_file(self):
        with tempfile.TemporaryDirectory() as directory:
            edge = EdgeController(DEFAULTS, Path(directory))
            with patch.object(edge, 'write', wraps=edge.write) as write:
                for at in (0, 5, 10, 30, 60):
                    edge.update(None, at)
                self.assertEqual(write.call_count, 1)

    def test_window_is_kept_and_lease_is_revoked_when_rate_falls(self):
        with tempfile.TemporaryDirectory() as directory:
            edge = EdgeController(DEFAULTS, Path(directory))
            with patch('codex_usage_watch.alert', return_value={'pid': 999}) as show, patch('codex_usage_watch.process_alive', return_value=True):
                signal = {'rate_pp_per_min': .125, 'remaining': 50}
                edge.update(signal, 100)
                edge.update(signal, 113)
                self.assertEqual(show.call_count, 1)
                self.assertTrue(json.loads(edge.path.read_text())['active'])
                edge.update(None, 114)
                self.assertFalse(json.loads(edge.path.read_text())['active'])


class RecordTests(unittest.TestCase):
    def record(self, used=64):
        return json.dumps({'type': 'event_msg', 'timestamp': '2026-01-01T00:00:00Z',
                           'payload': {'type': 'token_count', 'rate_limits': {
                               'limit_id': 'codex', 'primary': {
                                   'used_percent': used, 'window_minutes': 10080,
                                   'resets_at': 2000000000}}}}).encode()

    def test_missing_and_invalid_values_are_unavailable(self):
        for value in (None, '64', True, -1, 101, float('nan')):
            self.assertEqual(parse_line(self.record(value)), [])
        self.assertEqual(parse_line(b'{"not_quota": 1}'), [])

    def test_partial_write_and_desktop_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rollout-test.jsonl'
            meta = json.dumps({'payload': {'originator': 'Codex Desktop'}}).encode() + b'\n'
            line = self.record()
            path.write_bytes(meta + line[:40])
            reader = Reader(Path(directory))
            at = parse_line(line)[0].at
            self.assertEqual(reader.scan(at), [])
            with path.open('ab') as stream:
                stream.write(line[40:] + b'\n')
            self.assertEqual(len(reader.scan(at)), 1)
            self.assertEqual(reader.scan(at), [])
            cli = Path(directory) / 'rollout-cli.jsonl'
            cli.write_bytes(json.dumps({'payload': {'originator': 'codex_cli'}}).encode() + b'\n' + line + b'\n')
            self.assertEqual(reader.scan(at), [])

    def test_open_append_is_read_without_requiring_native_notification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'rollout-open.jsonl'
            meta = json.dumps({'payload': {'originator': 'Codex Desktop'}}).encode() + b'\n'
            path.write_bytes(meta + self.record() + b'\n')
            at = parse_line(self.record())[0].at
            reader = Reader(root)
            self.assertEqual(len(reader.scan(at)), 1)
            with path.open('ab') as writer, Wakeups(root, stop_event_name(root)) as wakeups:
                self.assertEqual(wakeups.wait(.02), 'timeout')
                writer.write(self.record(65) + b'\n')
                writer.flush()
                wakeups.wait(.02)
                self.assertFalse(wakeups.take_changes())
                with patch.object(Path, 'rglob', side_effect=AssertionError('Unexpected full rescan')):
                    self.assertEqual([s.used for s in reader.scan(at, discover=False)], [65])

    def test_native_new_file_event_requests_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with Wakeups(root, stop_event_name(root)) as wakeups:
                with (root / 'rollout-new.jsonl').open('wb') as writer:
                    writer.write(b'new file')
                    writer.flush()
                    self.assertEqual(wakeups.wait(2), 'source')
                    self.assertTrue(wakeups.take_changes())

    def test_named_stop_event_interrupts_wait_without_polling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = stop_event_name(root)
            with Wakeups(root, name) as wakeups:
                self.assertTrue(signal_stop(name))
                self.assertEqual(wakeups.wait(2, include_source=False), 'stop')


if __name__ == '__main__':
    unittest.main()
