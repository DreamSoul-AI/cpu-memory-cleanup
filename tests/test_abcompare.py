import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from cpu_memory_cleanup import abcompare
from cpu_memory_cleanup.__main__ import main

GIB = 1024 ** 3


def rec(available_gib, commit_gib=60.0, standby_gib=5.0, reads=1.0, inputs=10.0, disk=1e6, run="r"):
    return {"schema_version": 2, "mode": "read-only", "cleanup_performed": False,
            "started_at_utc": "2026-10-19T00:00:00+00:00", "finished_at_utc": "2026-10-19T00:00:01+00:00",
            "physical": {"available_bytes": int(available_gib * GIB)},
            "commit": {"used_bytes": int(commit_gib * GIB)},
            "cache": {"standby_core_bytes": 0, "standby_normal_bytes": int(standby_gib * GIB), "standby_reserve_bytes": 0},
            "rates": {"page_reads_per_sec": reads, "pages_input_per_sec": inputs,
                      "disk_read_bytes_per_sec": disk, "disk_write_bytes_per_sec": disk},
            "series": {"run_id": run}}


def window(n=9, **kw):
    return [rec(**kw) for _ in range(n)]


class ABCompareTests(unittest.TestCase):
    def test_null_aa_noise_is_not_flagged_as_spike_or_benefit(self):
        # Shape of the 2026-10-19 00:23 A/A smoke (no release in either window, 3 x 2 s each):
        # pages_input mean 1.7 vs 44.5 is natural desktop noise and must not read as refault cost.
        a = window(available_gib=8.91, commit_gib=61.36, standby_gib=8.09, reads=1.0, inputs=1.7, disk=1.1e5)
        b = window(available_gib=8.89, commit_gib=61.39, standby_gib=8.09, reads=3.2, inputs=44.5, disk=2.4e5)
        r = abcompare.compare(a, b)
        self.assertEqual(r["verdict"], "cosmetic")
        self.assertFalse(r["cleanup_performed"])

    def test_sustained_gain_without_spike_is_benefit(self):
        r = abcompare.compare(window(available_gib=8), window(available_gib=10))
        self.assertEqual(r["verdict"], "benefit-for-this-workload")
        self.assertFalse(r["cleanup_performed"])

    def test_commit_fall_is_benefit(self):
        r = abcompare.compare(window(available_gib=8, commit_gib=60), window(available_gib=8, commit_gib=58))
        self.assertEqual(r["verdict"], "benefit-for-this-workload")

    def test_gain_that_decays_back_is_cosmetic(self):
        b = [rec(available_gib=12)] * 3 + [rec(available_gib=10)] * 3 + [rec(available_gib=8)] * 3
        r = abcompare.compare(window(available_gib=8), b)
        self.assertEqual(r["verdict"], "cosmetic")

    def test_standby_moving_to_free_alone_is_cosmetic(self):
        r = abcompare.compare(window(available_gib=8, standby_gib=5), window(available_gib=8, standby_gib=1))
        self.assertEqual(r["verdict"], "cosmetic")

    def test_refault_spike_overrides_gain(self):
        r = abcompare.compare(window(available_gib=8, reads=2), window(available_gib=10, reads=40))
        self.assertEqual(r["verdict"], "cosmetic-or-harmful")
        self.assertTrue(r["refault"]["page_reads_per_sec"]["spike"])

    def test_floor_prevents_noise_spike_on_quiet_a(self):
        r = abcompare.compare(window(available_gib=8, reads=0.0), window(available_gib=10, reads=3.0))
        self.assertEqual(r["verdict"], "benefit-for-this-workload")

    def test_missing_rates_is_insufficient(self):
        a = window(available_gib=8)
        for x in a:
            x["rates"]["page_reads_per_sec"] = None
        r = abcompare.compare(a, window(available_gib=10))
        self.assertEqual(r["verdict"], "insufficient-data")

    def test_empty_window_raises(self):
        with self.assertRaises(ValueError):
            abcompare.compare([], window(available_gib=8))

    def test_module_makes_no_os_or_release_calls(self):
        source = Path(abcompare.__file__).read_text(encoding="utf-8")
        for banned in ("ctypes", "subprocess", "NtSetSystemInformation", "perform_release", "import os"):
            self.assertNotIn(banned, source)

    def test_cli_json_and_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            pa, pb = Path(tmp) / "a.jsonl", Path(tmp) / "b.jsonl"
            pa.write_text("\n".join(json.dumps(x) for x in window(available_gib=8)), encoding="utf-8")
            pb.write_text("\n".join(json.dumps(x) for x in window(available_gib=10)), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["--ab-compare", str(pa), str(pb), "--json"]), 0)
            self.assertEqual(json.loads(out.getvalue())["verdict"], "benefit-for-this-workload")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["--ab-compare", str(pa), str(pb)]), 0)
            self.assertIn("Verdict: benefit-for-this-workload", out.getvalue())

    def test_cli_rejects_other_modes(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main(["--ab-compare", "a", "b", "--sample"])


class ABNullCalibrationTests(unittest.TestCase):
    @staticmethod
    def burst_window(peak):
        # Shape of the 2026-10-19 00:24-00:34 A/A pair: quiet window with one natural two-sample burst.
        return window(n=10, available_gib=8.5, reads=2.0) + [rec(available_gib=8.5, reads=peak)] * 2 + \
            window(n=10, available_gib=8.5, reads=2.0)

    def test_aa_pair_with_natural_burst_trips_raw_rule(self):
        # Documents the false positive the null calibration exists to catch.
        r = abcompare.compare(window(n=22, available_gib=8.5, reads=2.0), self.burst_window(2000))
        self.assertEqual(r["verdict"], "cosmetic-or-harmful")
        self.assertIsNone(r["null_calibration"])

    def test_spike_within_null_ceiling_is_not_counted(self):
        null = [(window(n=22, available_gib=8.5, reads=2.0), self.burst_window(2000))]
        r = abcompare.compare(window(n=22, available_gib=8.5, reads=2.0), self.burst_window(1500), null_pairs=null)
        self.assertEqual(r["verdict"], "cosmetic")
        self.assertFalse(r["refault"]["page_reads_per_sec"]["beyond_null"])
        self.assertIn("within A/A noise", r["reason"])
        self.assertEqual(r["null_calibration"]["pairs_flagged_by_raw_rule"], 1)

    def test_spike_beyond_null_ceiling_still_counts(self):
        null = [(window(n=22, available_gib=8.5, reads=2.0), self.burst_window(200))]
        r = abcompare.compare(window(n=22, available_gib=8.5, reads=2.0),
                              [rec(available_gib=10.5, reads=3000)] * 22, null_pairs=null)
        self.assertEqual(r["verdict"], "cosmetic-or-harmful")
        self.assertTrue(r["refault"]["page_reads_per_sec"]["beyond_null"])

    def test_gain_with_suppressed_spike_is_benefit(self):
        null = [(window(n=22, available_gib=8.5, reads=2.0), self.burst_window(2000))]
        b = window(n=10, available_gib=10.5, reads=2.0) + [rec(available_gib=10.5, reads=1500)] * 2 + \
            window(n=10, available_gib=10.5, reads=2.0)
        r = abcompare.compare(window(n=22, available_gib=8.5, reads=2.0), b, null_pairs=null)
        self.assertEqual(r["verdict"], "benefit-for-this-workload")

    def test_cli_ab_null_requires_ab_compare(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--ab-null", "a.jsonl", "b.jsonl"])

    def test_cli_ab_null_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            paths = {}
            for name, rows in (("a", window(n=22, available_gib=8.5, reads=2.0)), ("b", self.burst_window(1500)),
                               ("n1", window(n=22, available_gib=8.5, reads=2.0)), ("n2", self.burst_window(2000))):
                paths[name] = Path(d) / f"{name}.jsonl"
                paths[name].write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["--ab-compare", str(paths["a"]), str(paths["b"]),
                             "--ab-null", str(paths["n1"]), str(paths["n2"])])
            self.assertEqual(code, 0)
            self.assertIn("A/A null calibration: 1 pair(s)", out.getvalue())
            self.assertIn("Verdict: cosmetic", out.getvalue())


if __name__ == "__main__":
    unittest.main()
