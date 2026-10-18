import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from cpu_memory_cleanup import follow, pressure
from cpu_memory_cleanup.__main__ import main


def rec(index, free=10.0, used=50.0, limit=100.0, owners=None, run="run-a"):
    return {
        "kind": "tree-series", "run_id": run, "index": index, "count": 9, "interval_s": 60,
        "local": f"2026-10-18 23:{index:02d}:00",
        "host": {"total_gib": 47.0, "free_gib": free, "commit_used_gib": used, "commit_limit_gib": limit},
        "top_owners": [{"owner": k, "processes": 1, "private_gib": v}
                       for k, v in (owners or {"A.exe": 1.0}).items()],
    }


def line(record):
    return (json.dumps(record) + "\n").encode("utf-8")


def no_sleep(_seconds):
    raise AssertionError("a single poll must not sleep")


class LiveTrackerTests(unittest.TestCase):
    def test_alert_once_when_streak_reaches_sustain(self):
        tracker = pressure.LiveTracker(sustain=2)
        events = [tracker.feed(rec(0)),
                  tracker.feed(rec(1, free=3.0, owners={"A.exe": 2.0})),
                  tracker.feed(rec(2, free=3.0, owners={"A.exe": 3.0})),
                  tracker.feed(rec(3, free=3.0))]
        self.assertEqual([e["alert"] for e in events], [False, False, True, False])
        self.assertEqual(events[2]["streak"], 2)
        self.assertEqual(events[2]["reasons"], ["free_below"])
        self.assertEqual(events[2]["growth_baseline"], "previous sample")
        self.assertEqual(events[2]["owner_growth"]["grown"],
                         [{"owner": "A.exe", "private_gib": 3.0, "delta_gib": 2.0}])
        self.assertEqual(tracker.alerts, 1)

    def test_recovery_and_new_run_reset_streak(self):
        tracker = pressure.LiveTracker(sustain=2)
        tracker.feed(rec(0, free=3.0))
        self.assertEqual(tracker.feed(rec(1))["streak"], 0)
        tracker.feed(rec(2, free=3.0))
        event = tracker.feed(rec(3, free=3.0, run="run-b"))
        self.assertEqual(event["streak"], 1)
        self.assertFalse(event["alert"])

    def test_first_sample_breach_uses_itself_as_baseline(self):
        event = pressure.LiveTracker(sustain=1).feed(rec(0, used=90.0))
        self.assertTrue(event["alert"])
        self.assertEqual(event["reasons"], ["commit_above"])
        self.assertEqual(event["commit_pct"], 90.0)
        self.assertEqual(event["growth_baseline"], "first breaching sample")

    def test_ignores_other_records_and_rejects_bad_sustain(self):
        tracker = pressure.LiveTracker()
        self.assertIsNone(tracker.feed({"kind": "other"}))
        self.assertIsNone(tracker.feed([1, 2]))
        self.assertEqual(tracker.samples, 0)
        for bad in (0, True, 1.5):
            with self.assertRaises(ValueError):
                pressure.LiveTracker(sustain=bad)


class FollowFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "series.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_appended_lines_across_partial_writes(self):
        self.path.write_bytes(line(rec(0)))
        second = line(rec(1, free=3.0))
        later = [second[:20], second[20:] + line(rec(2, free=3.0))]

        def fake_sleep(seconds):
            self.assertEqual(seconds, 5)
            with open(self.path, "ab") as handle:
                handle.write(later.pop(0))

        events = []
        summary = follow.follow_file(self.path, 3, 5, pressure.LiveTracker(sustain=2),
                                       events.append, sleep=fake_sleep)
        self.assertEqual([e["local"] for e in events],
                         ["2026-10-18 23:00:00", "2026-10-18 23:01:00", "2026-10-18 23:02:00"])
        self.assertTrue(events[-1]["alert"])
        self.assertEqual(summary["samples"], 3)
        self.assertEqual(summary["alerts"], 1)
        self.assertFalse(summary["actions_taken"])
        self.assertFalse(summary["pending_partial_line"])

    def test_skips_unparsable_lines_and_reports_partial_tail(self):
        self.path.write_bytes(b"not json\n" + line(rec(0)) + b'{"kind"')
        events = []
        summary = follow.follow_file(self.path, 1, 1, pressure.LiveTracker(), events.append,
                                       sleep=no_sleep)
        self.assertEqual(events[0]["kind"], "pressure-warning")
        self.assertEqual(summary["skipped_lines"], 1)
        self.assertEqual(summary["samples"], 1)
        self.assertTrue(summary["pending_partial_line"])

    def test_shrinking_log_stops(self):
        self.path.write_bytes(line(rec(0)) + line(rec(1)))

        def truncate(_seconds):
            self.path.write_bytes(line(rec(0)))

        with self.assertRaises(ValueError):
            follow.follow_file(self.path, 2, 1, pressure.LiveTracker(), lambda e: None, sleep=truncate)

    def test_bounds_are_enforced(self):
        self.path.write_bytes(b"")
        for polls, interval in ((0, 1), (follow.MAX_POLLS + 1, 1), (True, 1), (1, 0.5), (1, 3601)):
            with self.assertRaises(ValueError):
                follow.follow_file(self.path, polls, interval, pressure.LiveTracker(),
                                     lambda e: None, sleep=no_sleep)


class FollowCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "series.jsonl"
        self.path.write_bytes(line(rec(0)) + line(rec(1, free=3.0)))

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()

    def test_json_single_poll(self):
        code, out = self.run_main(["--pressure-follow", str(self.path), "--polls", "1",
                                   "--poll-interval", "1", "--sustain", "1", "--json"])
        self.assertEqual(code, 0)
        records = [json.loads(text) for text in out.splitlines()]
        self.assertEqual([r["kind"] for r in records],
                         ["pressure-sample", "pressure-sample", "pressure-follow-summary"])
        self.assertTrue(records[1]["alert"])
        self.assertEqual(records[2]["alerts"], 1)
        self.assertFalse(records[2]["actions_taken"])

    def test_text_single_poll(self):
        code, out = self.run_main(["--pressure-follow", str(self.path), "--polls", "1",
                                   "--poll-interval", "1", "--sustain", "1"])
        self.assertEqual(code, 0)
        self.assertIn("ALERT (proposal thresholds, no action taken)", out)
        self.assertIn("actions taken: NO", out)

    def test_flag_rules(self):
        bad = [["--pressure-follow", str(self.path)],
               ["--pressure-follow", str(self.path), "--polls", "1"],
               ["--polls", "1", "--poll-interval", "1"],
               ["--pressure-follow", str(self.path), "--polls", "1", "--poll-interval", "1",
                "--pressure", str(self.path)],
               ["--pressure-follow", str(self.path), "--polls", "1", "--poll-interval", "1", "--sample"],
               ["--pressure-follow", str(self.path), "--polls", "0", "--poll-interval", "1"]]
        for argv in bad:
            self.assertEqual(self.run_main(argv)[0], 2, argv)

    def test_missing_file_is_error(self):
        code, _ = self.run_main(["--pressure-follow", str(self.path) + ".missing", "--polls", "1",
                                 "--poll-interval", "1"])
        self.assertEqual(code, 1)


class StaticSafetyTests(unittest.TestCase):
    def test_follow_module_only_reads_one_file(self):
        source = Path(follow.__file__).read_text(encoding="utf-8")
        for token in ("import os", "subprocess", "ctypes", "windows", "kill", "terminate",
                      "EmptyWorkingSet", "EmptyStandbyList", "SetProcessWorkingSetSize",
                      "write", "unlink", "rename", "truncate"):
            self.assertNotIn(token, source)
        self.assertEqual(source.count("open("), 1)
        self.assertIn('open(path, "rb")', source)


if __name__ == "__main__":
    unittest.main()
