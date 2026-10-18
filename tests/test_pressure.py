import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cpu_memory_cleanup import pressure
from cpu_memory_cleanup.__main__ import main


def rec(index, free=10.0, used=50.0, limit=100.0, owners=None, run="run-a"):
    return {
        "kind": "tree-series", "run_id": run, "index": index, "count": 9, "interval_s": 60,
        "local": f"2026-10-18 23:{index:02d}:00",
        "host": {"total_gib": 47.0, "free_gib": free, "commit_used_gib": used, "commit_limit_gib": limit},
        "top_owners": [{"owner": k, "processes": 1, "private_gib": v}
                       for k, v in (owners or {"A.exe": 1.0}).items()],
    }


class PressureTests(unittest.TestCase):
    def test_sustain_threshold(self):
        records = [rec(0), rec(1, free=3.0), rec(2, free=3.5), rec(3)]
        run = pressure.evaluate(records, sustain=3)["runs"][0]
        self.assertEqual(run["breaching_samples"], 2)
        self.assertEqual(run["longest_breach_samples"], 2)
        self.assertEqual(run["episodes"], [])
        run = pressure.evaluate(records, sustain=2)["runs"][0]
        self.assertEqual(len(run["episodes"]), 1)
        self.assertEqual(run["episodes"][0]["reasons"], ["free_below"])
        self.assertEqual(run["episodes"][0]["min_free_gib"], 3.0)

    def test_episode_closes_at_end_of_file_and_commit_pct(self):
        records = [rec(0), rec(1, used=90.0), rec(2, used=95.0)]
        run = pressure.evaluate(records, sustain=2)["runs"][0]
        self.assertEqual(len(run["episodes"]), 1)
        episode = run["episodes"][0]
        self.assertEqual(episode["reasons"], ["commit_above"])
        self.assertEqual(episode["max_commit_pct"], 95.0)
        self.assertEqual(episode["end_local"], "2026-10-18 23:02:00")
        self.assertEqual(episode["growth_baseline"], "previous sample")

    def test_owner_growth_only_compares_owners_in_both(self):
        before = pressure.sample_view(rec(0, owners={"A.exe": 1.0, "B.exe": 2.0}))
        after = pressure.sample_view(rec(1, owners={"A.exe": 3.0, "B.exe": 1.0, "C.exe": 5.0}))
        growth = pressure.owner_growth(before, after)
        self.assertEqual(growth["grown"], [{"owner": "A.exe", "private_gib": 3.0, "delta_gib": 2.0}])
        self.assertEqual(growth["entered_top_list"], ["C.exe"])

    def test_missing_host_values_are_not_breaches(self):
        record = rec(0)
        record["host"] = {"free_gib": None, "commit_used_gib": 90.0, "commit_limit_gib": 0}
        run = pressure.evaluate([record, record, record], sustain=1)["runs"][0]
        self.assertEqual(run["breaching_samples"], 0)
        self.assertIsNone(run["min_free_gib"])
        self.assertIsNone(run["max_commit_pct"])

    def test_runs_are_separated_and_other_kinds_ignored(self):
        records = [rec(0, free=1.0, run="r1"), {"kind": "other"}, rec(0, free=1.0, run="r2")]
        report = pressure.evaluate(records, sustain=2)
        self.assertEqual([r["run_id"] for r in report["runs"]], ["r1", "r2"])
        self.assertTrue(all(r["episodes"] == [] for r in report["runs"]))
        self.assertFalse(report["actions_taken"])
        self.assertEqual(report["thresholds"]["status"], "proposal")

    def test_invalid_sustain(self):
        for value in (0, -1, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                pressure.evaluate([rec(0)], sustain=value)

    def test_text_report(self):
        text = pressure.format_report(pressure.evaluate(
            [rec(0, owners={"A.exe": 1.0}), rec(1, free=2.0, owners={"A.exe": 2.5})], sustain=1))
        self.assertIn("READ ONLY | actions taken: NO", text)
        self.assertIn("A.exe: +1.5 GiB", text)

    def test_cli_pressure_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(json.dumps(rec(i, free=2.0)) for i in range(3)))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["--pressure", path, "--json", "--sustain", "3",
                                       "--free-below-gib", "4", "--commit-above-pct", "85"]), 0)
            report = json.loads(out.getvalue())
            self.assertEqual(len(report["runs"][0]["episodes"]), 1)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--pressure", os.path.join(tmp, "nope.jsonl")]), 1)

    def test_cli_pressure_rejects_sampling_and_threshold_flags_alone(self):
        for args in (["--pressure", "x.jsonl", "--sample", "--interval", "1", "--count", "1"],
                     ["--pressure", "x.jsonl", "--analyze", "y.jsonl"],
                     ["--sustain", "3"], ["--free-below-gib", "2"],
                     ["--pressure", "x.jsonl", "--sustain", "0"]):
            with self.subTest(args=args), patch("cpu_memory_cleanup.__main__.snapshot") as snap:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                    main(args)
                self.assertEqual(exc.exception.code, 2)
                snap.assert_not_called()

    def test_module_makes_no_os_calls(self):
        source = Path(pressure.__file__).read_text(encoding="utf-8")
        for token in ("import os", "subprocess", "ctypes", "windows", "kill", "terminate",
                      "EmptyWorkingSet", "EmptyStandbyList", "SetProcessWorkingSetSize", "open("):
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
