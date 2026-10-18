import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from cpu_memory_cleanup import privcheck as pc
from cpu_memory_cleanup import release as rel
from cpu_memory_cleanup.__main__ import main

NAME = "SeProfileSingleProcessPrivilege"


def report():
    return {"physical": {}, "commit": {}, "cache": {}}


class PrivilegePreflightTests(unittest.TestCase):
    def test_held_but_disabled_is_not_ready(self):
        rec = pc.check(query=lambda: [(NAME, 0), ("SeChangeNotifyPrivilege", 3)])
        self.assertEqual(rec["privileges"], [{"name": NAME, "held": True, "enabled": False, "enabled_by_default": False}])
        self.assertIs(rec["ready"], False)
        self.assertIn("expect NTSTATUS 0xC0000061", pc.format_check(rec))

    def test_enabled_is_ready(self):
        rec = pc.check(query=lambda: [(NAME, pc.SE_PRIVILEGE_ENABLED | pc.SE_PRIVILEGE_ENABLED_BY_DEFAULT)])
        self.assertIs(rec["ready"], True)
        self.assertTrue(rec["privileges"][0]["enabled_by_default"])

    def test_missing_and_removed_are_not_held(self):
        for entries in ([], [(NAME, pc.SE_PRIVILEGE_REMOVED | pc.SE_PRIVILEGE_ENABLED)]):
            rec = pc.check(query=lambda e=entries: e)
            self.assertFalse(rec["privileges"][0]["held"])
            self.assertIs(rec["ready"], False)

    def test_query_failure_is_reported_not_raised(self):
        def boom():
            raise OSError("no token")
        rec = pc.check(query=boom)
        self.assertIsNone(rec["ready"])
        self.assertIn("no token", rec["error"])
        self.assertIn("unknown", pc.format_check(rec))

    def test_source_is_query_only(self):
        src = Path(pc.__file__).read_text(encoding="utf-8-sig")
        for banned in ("AdjustTokenPrivileges", "TOKEN_ADJUST", "ShellExecute", "runas",
                       "TerminateProcess", "NtSetSystemInformation", "RegSet", "Stop-Process"):
            self.assertNotIn(banned, src)
        self.assertIn("TOKEN_QUERY", src)

    def test_release_records_preflight_and_warns(self):
        rec = rel.perform_release("standby", nt_call=lambda cmd: 0xC0000061,
                                  snapshot_fn=iter([report(), report()]).__next__,
                                  preflight_fn=lambda: pc.check(query=lambda: [(NAME, 0)]))
        self.assertIs(rec["preflight"]["ready"], False)
        self.assertTrue(any("0xC0000061 is expected" in w for w in rec["warnings"]))
        self.assertIn("held-disabled", rel.format_release(rec))

    def test_injected_nt_call_skips_real_preflight(self):
        with patch("cpu_memory_cleanup.privcheck._default_query") as real:
            rec = rel.perform_release("standby", nt_call=lambda cmd: 0,
                                      snapshot_fn=iter([report(), report()]).__next__)
        real.assert_not_called()
        self.assertIsNone(rec["preflight"])

    def test_cli_preflight_is_readonly(self):
        fake = {"schema_version": 1, "mode": "release-preflight", "required": [NAME],
                "privileges": [{"name": NAME, "held": True, "enabled": False, "enabled_by_default": False}],
                "ready": False, "error": None, "note": "n"}
        with patch("cpu_memory_cleanup.__main__.privilege_check", return_value=fake) as chk, \
             patch("cpu_memory_cleanup.__main__.perform_release") as perf, \
             patch("cpu_memory_cleanup.__main__.snapshot") as snap:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["--release-preflight", "--json"]), 0)
        chk.assert_called_once()
        perf.assert_not_called()
        snap.assert_not_called()
        self.assertIs(json.loads(out.getvalue())["ready"], False)

    def test_cli_preflight_combos_rejected(self):
        for args in (["--release-preflight", "--release"], ["--release-preflight", "--sample"],
                     ["--release-preflight", "--top", "3"]):
            with self.subTest(args=args), patch("cpu_memory_cleanup.__main__.privilege_check") as chk, \
                 patch("cpu_memory_cleanup.__main__.perform_release") as perf:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                    main(args)
                self.assertEqual(exc.exception.code, 2)
                chk.assert_not_called()
                perf.assert_not_called()


if __name__ == "__main__":
    unittest.main()