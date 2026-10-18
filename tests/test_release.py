import ast
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from cpu_memory_cleanup import release as rel
from cpu_memory_cleanup.__main__ import main

GIB = 1024**3


def fake_report(available, commit, standby, free):
    cache = {"standby_core_bytes": standby, "standby_normal_bytes": 0, "standby_reserve_bytes": 0,
             "free_zero_bytes": free, "modified_bytes": 0, "cache_bytes": 0}
    return {"physical": {"available_bytes": available, "used_bytes": 0},
            "commit": {"used_bytes": commit, "limit_bytes": 100 * GIB},
            "cache": cache}


def snapshot_seq(*reports):
    it = iter(reports)
    return lambda: next(it)


class ReleaseTests(unittest.TestCase):
    def test_commands_mapping(self):
        self.assertEqual([c for _, _, c in rel.commands_for("working-set")], [2])
        self.assertEqual([c for _, _, c in rel.commands_for("standby")], [4])
        self.assertEqual([c for _, _, c in rel.commands_for("both")], [2, 4])
        with self.assertRaises(ValueError):
            rel.commands_for("flush-modified")

    def test_both_success_deltas_and_no_real_call(self):
        calls = []
        before = fake_report(10 * GIB, 60 * GIB, 6 * GIB, 1 * GIB)
        after = fake_report(11 * GIB, 60 * GIB, 1 * GIB, 6 * GIB)
        rec = rel.perform_release("both", nt_call=lambda cmd: calls.append(cmd) or 0,
                                  snapshot_fn=snapshot_seq(before, after))
        self.assertEqual(calls, [2, 4])
        self.assertTrue(rec["cleanup_performed"])
        self.assertEqual(rec["mode"], "release")
        self.assertTrue(all(a["ok"] for a in rec["actions"]))
        self.assertEqual(rec["deltas"]["standby_bytes_delta"], -5 * GIB)
        self.assertEqual(rec["deltas"]["commit_used_bytes_delta"], 0)

    def test_single_mechanisms(self):
        for what, cmd in (("working-set", 2), ("standby", 4)):
            calls = []
            rec = rel.perform_release(what, nt_call=lambda c: calls.append(c) or 0,
                                      snapshot_fn=snapshot_seq(fake_report(1, 2, 3, 4), fake_report(1, 2, 3, 4)))
            self.assertEqual(calls, [cmd])
            self.assertTrue(rec["cleanup_performed"])

    def test_privilege_failure_is_plain_and_graceful(self):
        rec = rel.perform_release("standby", nt_call=lambda cmd: 0xC0000061,
                                  snapshot_fn=snapshot_seq(fake_report(1, 2, 3, 4), fake_report(1, 2, 3, 4)))
        self.assertFalse(rec["cleanup_performed"])
        self.assertEqual(rec["actions"][0]["ntstatus_hex"], "0xc0000061")
        self.assertIn("PRIVILEGE_NOT_HELD", rec["actions"][0]["ntstatus_name"])

    def test_nt_exception_and_partial_success(self):
        def flaky(cmd):
            if cmd == 2:
                raise OSError("denied")
            return 0
        rec = rel.perform_release("both", nt_call=flaky,
                                  snapshot_fn=snapshot_seq(fake_report(1, 2, 3, 4), fake_report(1, 2, 3, 4)))
        self.assertTrue(rec["cleanup_performed"])  # standby succeeded
        self.assertFalse(rec["actions"][0]["ok"])
        self.assertIn("denied", rec["actions"][0]["error"])

    def test_no_forbidden_mechanisms_in_release_source(self):
        src = Path(rel.__file__).read_text(encoding="utf-8-sig")
        for banned in ("TerminateProcess", "ShellExecute", "runas", "AdjustTokenPrivileges",
                       "OpenProcess", "EmptyWorkingSet(", "taskkill", "Stop-Process"):
            self.assertNotIn(banned, src)
        tree = ast.parse(src)
        self.assertIn("NtSetSystemInformation", src)

    # --- CLI contract ---
    def test_bare_invocation_stays_readonly(self):
        with patch("cpu_memory_cleanup.__main__.snapshot", return_value={"x": 1}) as snap, \
             patch("cpu_memory_cleanup.__main__.perform_release") as perf, \
             patch("cpu_memory_cleanup.__main__.render", return_value="READ ONLY"):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([]), 0)
        snap.assert_called_once()
        perf.assert_not_called()

    def test_release_flag_is_only_entry(self):
        fake = {"cleanup_performed": True, "mode": "release"}
        with patch("cpu_memory_cleanup.__main__.perform_release", return_value=fake) as perf, \
             patch("cpu_memory_cleanup.__main__.snapshot") as snap:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["--release", "--json"]), 0)
        perf.assert_called_once_with("both")
        snap.assert_not_called()
        self.assertTrue(json.loads(out.getvalue())["cleanup_performed"])

    def test_release_failure_exit_1_not_traceback(self):
        fake = {"cleanup_performed": False, "mode": "release",
                "release_what": "both", "started_at_utc": "t", "finished_at_utc": "t",
                "before": {}, "after": {}, "deltas": {}, "actions": [], "warnings": []}
        # Use real formatter path (no --json) via patched perform returning minimal record:
        fake["actions"] = [{"what": "standby", "mechanism": "MemoryPurgeStandbyList", "command": 4,
                            "ntstatus": 0xC0000061, "ntstatus_hex": "0xc0000061",
                            "ntstatus_name": "STATUS_PRIVILEGE_NOT_HELD", "ok": False, "error": None}]
        with patch("cpu_memory_cleanup.__main__.perform_release", return_value=fake):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["--release"]), 1)
        self.assertIn("cleanup performed: NO", out.getvalue())

    def test_unknown_cleanup_flags_and_release_combos_rejected(self):
        rejected = (["--execute"], ["--clean"], ["--release-what", "standby"],
                    ["--release", "--sample", "--interval", "1", "--count", "1", "--json"],
                    ["--release", "--analyze", "x.jsonl"], ["--release-what", "bogus", "--release"])
        for args in rejected:
            with self.subTest(args=args), patch("cpu_memory_cleanup.__main__.perform_release") as perf, \
                 patch("cpu_memory_cleanup.__main__.snapshot") as snap:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                    main(args)
                self.assertEqual(exc.exception.code, 2)
                perf.assert_not_called()
                snap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
