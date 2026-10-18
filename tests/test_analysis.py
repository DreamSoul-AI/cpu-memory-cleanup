import contextlib
import io
import json
import unittest
from unittest.mock import patch

from cpu_memory_cleanup import analysis
from cpu_memory_cleanup.__main__ import main


def record(index, available=10 * 1024**3, commit=20 * 1024**3, rates=None, warnings=None):
    return {
        "started_at_utc": f"2026-10-18T12:00:{index:02d}+00:00",
        "physical": {"available_bytes": available},
        "commit": {"used_bytes": commit},
        "cache": {"standby_core_bytes": 1, "standby_normal_bytes": 2, "standby_reserve_bytes": 3},
        "rates": rates if rates is not None else {
            "page_reads_per_sec": 1.0, "pages_input_per_sec": 2.0,
            "disk_read_bytes_per_sec": 3.0, "disk_write_bytes_per_sec": 4.0},
        "warnings": warnings or [],
        "series": {"run_id": "run-a", "index": index, "count": 3},
        "cleanup_performed": False,
    }


class AnalysisTests(unittest.TestCase):
    def test_parse_skips_blank_and_rejects_bad_json(self):
        lines = [json.dumps(record(0)), "", "  ", json.dumps(record(1))]
        self.assertEqual(len(analysis.parse_records(lines)), 2)
        with self.assertRaises(ValueError):
            analysis.parse_records(["{not json"])
        with self.assertRaises(ValueError):
            analysis.parse_records(["[1,2]"])

    def test_stats_median_percentiles_and_missing(self):
        stats = analysis.describe([None, 1, 2, 3, 4])
        self.assertEqual(stats["count"], 4)
        self.assertEqual(stats["missing"], 1)
        self.assertEqual(stats["min"], 1.0)
        self.assertEqual(stats["median"], 2.5)
        self.assertEqual(stats["max"], 4.0)
        self.assertAlmostEqual(stats["p90"], 3.7)
        empty = analysis.describe([None, None])
        self.assertIsNone(empty["median"])

    def test_summary_counts_first_tick_and_overrun_warnings(self):
        records = [
            record(0, rates={"page_reads_per_sec": None, "pages_input_per_sec": None,
                             "disk_read_bytes_per_sec": None, "disk_write_bytes_per_sec": None},
                   warnings=["page_reads_per_sec unavailable until the next sample: no previous PDH interval",
                             "pages_input_per_sec unavailable until the next sample: no previous PDH interval"]),
            record(1, warnings=["Previous interval overrun: sample ran longer than the interval"]),
            record(2),
        ]
        summary = analysis.summarize_records(records)
        self.assertEqual(summary["sample_count"], 3)
        self.assertEqual(summary["warning_count"], 3)
        self.assertEqual(summary["overrun_count"], 1)
        self.assertEqual(summary["first_tick_rate_warning_count"], 2)
        self.assertEqual(summary["metrics"]["page_reads_per_sec"]["count"], 2)
        self.assertEqual(summary["metrics"]["page_reads_per_sec"]["missing"], 1)
        self.assertEqual(summary["metrics"]["standby_total_bytes"]["median"], 6.0)
        self.assertFalse(summary["cleanup_performed"])

    def test_missing_standby_part_is_not_zero(self):
        bad = record(0)
        bad["cache"]["standby_normal_bytes"] = None
        self.assertIsNone(analysis.metric_values(bad)["standby_total_bytes"])

    def test_transition_detection_threshold_and_null_gap(self):
        gib = 1024**3
        records = [record(0, available=10 * gib, commit=20 * gib),
                   record(1, available=12 * gib, commit=20 * gib),
                   record(2, available=12 * gib + 1, commit=None),
                   record(3, available=30 * gib, commit=5 * gib)]
        found = analysis.detect_transitions(records, threshold_bytes=gib)
        pairs = [(item["index"], item["metric"]) for item in found]
        self.assertIn((1, "available_bytes"), pairs)
        self.assertNotIn((2, "available_bytes"), pairs)
        # index 3 commit compares against None at index 2, so no commit transition there
        self.assertNotIn((3, "commit_used_bytes"), pairs)
        self.assertIn((3, "available_bytes"), pairs)

    def test_cli_analyze_json_and_missing_file(self):
        lines = "\n".join(json.dumps(record(i)) for i in range(2))
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(lines)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["--analyze", path, "--json"]), 0)
            summary = json.loads(out.getvalue())
            self.assertEqual(summary["sample_count"], 2)
            err = io.StringIO()
            with contextlib.redirect_stdout(err):
                self.assertEqual(main(["--analyze", os.path.join(tmp, "nope.jsonl")]), 1)

    def test_cli_analyze_rejects_series_flags_and_execution(self):
        for args in (["--analyze", "x.jsonl", "--sample", "--interval", "1", "--count", "1"],
                     ["--execute"], ["--clean"]):
            with self.subTest(args=args), patch("cpu_memory_cleanup.__main__.snapshot") as snap:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                    main(args)
                self.assertEqual(exc.exception.code, 2)
                snap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
