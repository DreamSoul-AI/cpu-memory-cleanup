import contextlib
import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cpu_memory_cleanup import abprotocol
from cpu_memory_cleanup.__main__ import main

GIB = 1024 ** 3
READY = {"ready": True, "error": None, "privileges": [{"name": "SeProfileSingleProcessPrivilege", "held": True, "enabled": True}]}
NOT_READY = {"ready": False, "error": None, "privileges": [{"name": "SeProfileSingleProcessPrivilege", "held": True, "enabled": False}]}


def rec(available_gib=8.0, commit_gib=60.0, reads=2.0, inputs=15.0, disk=1.4e5):
    return {"schema_version": 2, "mode": "read-only", "cleanup_performed": False,
            "started_at_utc": "2026-10-19T00:00:00+00:00", "finished_at_utc": "2026-10-19T00:00:01+00:00",
            "physical": {"available_bytes": int(available_gib * GIB)},
            "commit": {"used_bytes": int(commit_gib * GIB)},
            "cache": {"standby_core_bytes": 0, "standby_normal_bytes": 5 * GIB, "standby_reserve_bytes": 0},
            "rates": {"page_reads_per_sec": reads, "pages_input_per_sec": inputs,
                      "disk_read_bytes_per_sec": disk, "disk_write_bytes_per_sec": disk},
            "series": {"run_id": "r"}}


def forbidden(*_a, **_k):
    raise AssertionError("must not be called")


class Recorder:
    def __init__(self, release_ok=True):
        self.calls = []
        self.release_ok = release_ok
        self.released = False

    def sample(self, count, interval):
        self.calls.append(("sample", count, interval))
        gib = 10.5 if self.released else 8.0
        self.released = False
        return iter([rec(available_gib=gib) for _ in range(count)])

    def release(self):
        self.calls.append(("release",))
        self.released = self.release_ok
        status = 0 if self.release_ok else 0xC0000061
        return {"cleanup_performed": self.release_ok,
                "actions": [{"ntstatus_hex": f"0x{status:08x}"}]}


class PlanTests(unittest.TestCase):
    def test_pattern_requires_null_and_release_pairs(self):
        self.assertEqual(abprotocol.parse_pattern("aa, ab"), ("AA", "AB"))
        for bad in ("AB,AB", "AA", "AA,BA", "", ",".join(["AA", "AB"] * 5)):
            with self.assertRaises(ValueError):
                abprotocol.parse_pattern(bad)

    def test_plan_releases_only_before_second_window_of_ab_pairs(self):
        plan = abprotocol.build_plan(("AA", "AB", "AA", "AB"), 10, 30)
        self.assertEqual(plan["releases_planned"], 2)
        flagged = [w["file"] for w in plan["windows"] if w["release_before"]]
        self.assertEqual(flagged, ["pair2-AB-2.jsonl", "pair4-AB-2.jsonl"])
        self.assertEqual(plan["estimated_minutes"], 40.0)
        with self.assertRaises(ValueError):
            abprotocol.build_plan(("AA", "AB"), 10, 2)


class GuardTests(unittest.TestCase):
    def test_recent_series_log_blocks_and_old_one_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp, "tree-series-age.jsonl")
            old.write_text("{}\n")
            os.utime(old, (time.time() - 3600, time.time() - 3600))
            self.assertEqual(abprotocol.busy_series(tmp), [])
            Path(tmp, "tree-series-residual.jsonl").write_text("{}\n")
            Path(tmp, "aa-A1.jsonl").write_text("{}\n")  # not a tree-series log
            busy = abprotocol.busy_series(tmp)
            self.assertEqual([b["file"] for b in busy], ["tree-series-residual.jsonl"])

    def test_privilege_not_enabled_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = abprotocol.check_guards(tmp, lambda: NOT_READY)
            self.assertFalse(g["ok"])
            self.assertIn("0xC0000061", g["blockers"][0])
            self.assertTrue(abprotocol.check_guards(tmp, lambda: READY)["ok"])


class RunTests(unittest.TestCase):
    def test_dry_run_samples_and_releases_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "ab")
            r = abprotocol.run_protocol(abprotocol.build_plan(), out, log_dir=tmp, sample_fn=forbidden,
                                        release_fn=forbidden, preflight_fn=lambda: READY)
            self.assertFalse(r["cleanup_performed"])
            self.assertFalse(out.exists())
            self.assertTrue(r["status"].startswith("dry-run"))

    def test_live_aborts_before_sampling_while_series_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "tree-series-residual.jsonl").write_text("{}\n")
            r = abprotocol.run_protocol(abprotocol.build_plan(), Path(tmp, "ab"), live=True, log_dir=tmp,
                                        sample_fn=forbidden, release_fn=forbidden, preflight_fn=lambda: READY)
            self.assertTrue(r["status"].startswith("aborted before any sampling"))
            self.assertFalse(r["cleanup_performed"])

    def test_live_aborts_when_privilege_not_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = abprotocol.run_protocol(abprotocol.build_plan(), Path(tmp, "ab"), live=True, log_dir=tmp,
                                        sample_fn=forbidden, release_fn=forbidden, preflight_fn=lambda: NOT_READY)
            self.assertIn("0xC0000061", r["status"])

    def test_live_releases_once_per_ab_pair_and_summarises_against_nulls(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Recorder()
            out = Path(tmp, "ab")
            r = abprotocol.run_protocol(abprotocol.build_plan(("AA", "AB", "AA", "AB"), 1, 4), out, live=True,
                                        log_dir=tmp, sample_fn=fake.sample, release_fn=fake.release,
                                        preflight_fn=lambda: READY)
            self.assertEqual(r["status"], "completed")
            kinds = [c[0] for c in fake.calls]
            self.assertEqual(kinds, ["sample"] * 3 + ["release", "sample"] + ["sample"] * 3 + ["release", "sample"])
            self.assertTrue(r["cleanup_performed"])
            self.assertTrue((out / "protocol.json").is_file())
            self.assertTrue((out / "pair2-release.json").is_file())
            s = r["summary"]
            self.assertEqual(s["null_pairs"], 2)
            ab = [p for p in s["pairs"] if p["kind"] == "AB"]
            self.assertEqual([p["verdict"] for p in ab], ["benefit-for-this-workload"] * 2)
            self.assertTrue(all(p["null_pairs_used"] == 2 for p in ab))
            aa = [p for p in s["pairs"] if p["kind"] == "AA"]
            self.assertEqual([p["verdict"] for p in aa], ["cosmetic"] * 2)

    def test_failed_release_stops_and_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Recorder(release_ok=False)
            r = abprotocol.run_protocol(abprotocol.build_plan(("AA", "AB", "AA", "AB"), 1, 4), Path(tmp, "ab"),
                                        live=True, log_dir=tmp, sample_fn=fake.sample, release_fn=fake.release,
                                        preflight_fn=lambda: READY)
            self.assertEqual([c[0] for c in fake.calls].count("release"), 1)
            self.assertIn("not retried", r["status"])
            self.assertFalse(r["cleanup_performed"])
            self.assertEqual(r["events"][-1]["ntstatus"], ["0xc0000061"])

    def test_live_refuses_reused_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "ab")
            out.mkdir()
            (out / "pair1-AA-1.jsonl").write_text("{}\n")
            r = abprotocol.run_protocol(abprotocol.build_plan(), out, live=True, log_dir=tmp,
                                        sample_fn=forbidden, release_fn=forbidden, preflight_fn=lambda: READY)
            self.assertIn("fresh directory", r["status"])


class CliTests(unittest.TestCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_protocol_flags_need_protocol_and_exclude_release(self):
        self.assertEqual(self.run_main(["--ab-live"])[0], 2)
        self.assertEqual(self.run_main(["--ab-protocol", "x", "--release"])[0], 2)
        self.assertEqual(self.run_main(["--ab-protocol", "x", "--sample"])[0], 2)

    def test_cli_dry_run_reports_blockers_and_touches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "ab")
            with mock.patch.object(abprotocol, "_default_preflight", lambda: NOT_READY), \
                    mock.patch.object(abprotocol, "_default_sample", forbidden), \
                    mock.patch.object(abprotocol, "_default_release", forbidden):
                code, text, _ = self.run_main(["--ab-protocol", str(out)])
            self.assertEqual(code, 0)
            self.assertIn("cleanup performed: NO", text)
            self.assertIn("BLOCKED", text)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()