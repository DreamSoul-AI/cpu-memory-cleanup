import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from cpu_memory_cleanup import treesummary
from cpu_memory_cleanup.__main__ import main


def bucket(p, g):
    return {"processes": p, "private_gib": g}


def rec(index, procs=600, tree=200, gt3=0, residual=None, run="run-a", ages=True):
    record = {
        "kind": "tree-series", "run_id": run, "index": index, "count": 3, "interval_s": 60,
        "local": f"2026-10-18 23:{index:02d}:00",
        "host": {"total_gib": 47.0, "free_gib": 10.0 + index, "commit_used_gib": 60.0, "commit_limit_gib": 95.0},
        "processes": procs, "born_since_prev": 2, "exited_since_prev": 1,
        "codex_tree": {"processes": tree, "private_gib": 4.0 + index, "server_mjs": 10,
                       "server_mjs_private_gib": 1.0, "born_since_prev": 1, "exited_since_prev": 3},
    }
    if ages:
        record["codex_tree"]["age_buckets"] = [
            {"kind": "server_mjs", "oldest_minutes": 100.0 + index,
             "lt10m": bucket(1, 0.1), "10to60m": bucket(2, 0.2), "1to3h": bucket(3, 0.3),
             "gt3h": bucket(gt3, 0.07 * gt3), "unknown": bucket(0, 0)}]
    if residual is not None:
        record["commit_residual"] = {"residual_gib": residual, "residual_pct_of_commit": residual / 60.0 * 100}
    return record


class TreeSummaryTests(unittest.TestCase):
    def test_age_buckets_and_totals(self):
        run = treesummary.summarize([rec(0, gt3=0), rec(1, gt3=4), rec(2, gt3=8)])["runs"][0]
        self.assertEqual(run["samples"], 3)
        gt3 = run["age"]["server_mjs"]["buckets"]["gt3h"]["processes"]
        self.assertEqual((gt3["first"], gt3["last"], gt3["max"]), (0, 8, 8))
        self.assertEqual(run["age"]["server_mjs"]["oldest_minutes"]["max"], 102.0)
        self.assertEqual(run["age"]["cua_repl"]["samples_with_field"], 0)
        self.assertEqual(run["born_total"], 4)
        self.assertEqual(run["codex_tree"]["exited_total"], 6)
        self.assertEqual(run["host_free_gib"]["min"], 10.0)

    def test_missing_fields_are_null_not_guessed(self):
        run = treesummary.summarize([rec(0, ages=False), rec(1, ages=False)])["runs"][0]
        self.assertEqual(run["age"]["server_mjs"]["samples_with_field"], 0)
        self.assertIsNone(run["age"]["server_mjs"]["buckets"]["gt3h"]["processes"]["max"])
        self.assertEqual(run["commit_residual"]["residual_gib"]["samples"], 0)
        self.assertIsNone(run["commit_residual"]["pearson_residual_vs_processes"])
        text = treesummary.format_summary(treesummary.summarize([rec(0, ages=False)]))
        self.assertIn("no age_buckets", text)
        self.assertIn("no commit_residual", text)

    def test_residual_correlation(self):
        records = [rec(0, procs=600, residual=9.0), rec(1, procs=700, residual=9.5), rec(2, procs=800, residual=10.0)]
        res = treesummary.summarize(records)["runs"][0]["commit_residual"]
        self.assertEqual(res["pearson_residual_vs_processes"], 1.0)
        self.assertEqual(res["residual_gib"]["max"], 10.0)

    def test_runs_split_and_other_kinds_ignored(self):
        records = [rec(0), {"kind": "sample"}, rec(0, run="run-b"), rec(1)]
        summary = treesummary.summarize(records)
        self.assertFalse(summary["actions_taken"])
        self.assertEqual([r["run_id"] for r in summary["runs"]], ["run-a", "run-b"])
        self.assertEqual(summary["runs"][0]["samples"], 2)

    def test_cli_text_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "series.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in [rec(0), rec(1, gt3=2)]) + "\n", encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["--tree-summary", str(path)]), 0)
            self.assertIn("READ ONLY", out.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["--tree-summary", str(path), "--json"]), 0)
            self.assertEqual(json.loads(out.getvalue())["runs"][0]["samples"], 2)

    def test_cli_rejects_combinations(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main(["--tree-summary", "x.jsonl", "--sample"])
            with self.assertRaises(SystemExit):
                main(["--tree-summary", "x.jsonl", "--pressure", "y.jsonl"])

    def test_module_is_static_file_parsing_only(self):
        source = (Path(treesummary.__file__)).read_text(encoding="utf-8")
        for banned in ("ctypes", "subprocess", "os.system", "windows", "open(", "kill", "terminate"):
            self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main()
