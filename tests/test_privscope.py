import contextlib
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from cpu_memory_cleanup import abprotocol
from cpu_memory_cleanup import privcheck as pc
from cpu_memory_cleanup import privscope as ps
from cpu_memory_cleanup import release as rel

NAME = "SeProfileSingleProcessPrivilege"


def report():
    return {"physical": {}, "commit": {}, "cache": {}}


class FakeAdjust:
    def __init__(self, was=False, fail_enable=None, fail_disable=None, log=None):
        self.was, self.fail_enable, self.fail_disable = was, fail_enable, fail_disable
        self.log = [] if log is None else log

    def __call__(self, enable):
        self.log.append("enable" if enable else "disable")
        if enable and self.fail_enable:
            raise OSError(self.fail_enable)
        if not enable and self.fail_disable:
            raise OSError(self.fail_disable)
        return self.was if enable else True


class ScopedEnableTests(unittest.TestCase):
    def test_enable_then_disable_around_body(self):
        log = []
        with ps.scoped_enable(FakeAdjust(log=log)) as rec:
            log.append("body")
        self.assertEqual(log, ["enable", "body", "disable"])
        self.assertTrue(rec["enabled_by_tool"])
        self.assertTrue(rec["restored"])
        self.assertIn("then disabled again", ps.describe(rec))

    def test_disabled_again_even_if_body_raises(self):
        log = []
        with self.assertRaises(RuntimeError):
            with ps.scoped_enable(FakeAdjust(log=log)):
                raise RuntimeError("boom")
        self.assertEqual(log, ["enable", "disable"])

    def test_already_enabled_is_left_as_found(self):
        log = []
        with ps.scoped_enable(FakeAdjust(was=True, log=log)) as rec:
            pass
        self.assertEqual(log, ["enable"])
        self.assertFalse(rec["enabled_by_tool"])
        self.assertIsNone(rec["restored"])

    def test_not_held_is_recorded_not_raised_or_retried(self):
        log = []
        with ps.scoped_enable(FakeAdjust(fail_enable="not held (ERROR_NOT_ALL_ASSIGNED)", log=log)) as rec:
            pass
        self.assertEqual(log, ["enable"])
        self.assertIn("ERROR_NOT_ALL_ASSIGNED", rec["error"])
        self.assertFalse(rec["enabled_by_tool"])

    def test_disable_failure_is_recorded(self):
        with ps.scoped_enable(FakeAdjust(fail_disable="nope")) as rec:
            pass
        self.assertIs(rec["restored"], False)
        self.assertIn("DISABLE FAILED", ps.describe(rec))

    def test_source_touches_only_own_token_and_one_privilege(self):
        src = Path(ps.__file__).read_text(encoding="utf-8-sig")
        for banned in ("ShellExecute", "runas", "TerminateProcess", "NtSetSystemInformation", "RegSet",
                       "Stop-Process", "OpenProcess(", "DuplicateToken", "SE_PRIVILEGE_REMOVED", "subprocess"):
            self.assertNotIn(banned, src)
        privileges = {m for m in __import__("re").findall(r"Se[A-Z][A-Za-z]+Privilege", src)}
        self.assertEqual(privileges, {NAME})
        self.assertIn("GetCurrentProcess", src)


class ReleaseScopeTests(unittest.TestCase):
    def test_nt_calls_run_inside_scope(self):
        log = []

        def nt(cmd):
            log.append(f"nt{cmd}")
            return 0
        rec = rel.perform_release("both", nt_call=nt, snapshot_fn=iter([report(), report()]).__next__,
                                  privilege_scope=lambda: ps.scoped_enable(FakeAdjust(log=log)))
        self.assertEqual(log, ["enable", "nt2", "nt4", "disable"])
        self.assertTrue(rec["cleanup_performed"])
        self.assertTrue(rec["privilege_scope"]["restored"])
        self.assertIn("privilege: enabled only for the release calls", rel.format_release(rec))

    def test_enable_failure_still_reports_ntstatus(self):
        rec = rel.perform_release("standby", nt_call=lambda cmd: 0xC0000061,
                                  snapshot_fn=iter([report(), report()]).__next__,
                                  privilege_scope=lambda: ps.scoped_enable(FakeAdjust(fail_enable="not held")))
        self.assertFalse(rec["cleanup_performed"])
        self.assertEqual(rec["actions"][0]["ntstatus_hex"], "0xc0000061")
        self.assertTrue(any("never elevates" in w for w in rec["warnings"]))

    def test_injected_nt_call_without_scope_never_touches_token(self):
        with patch.object(ps, "_default_adjust") as real:
            rec = rel.perform_release("standby", nt_call=lambda cmd: 0,
                                      snapshot_fn=iter([report(), report()]).__next__)
        real.assert_not_called()
        self.assertIsNone(rec["privilege_scope"])

    def test_live_default_uses_scope(self):
        log = []
        with patch.object(rel, "_default_nt_call", lambda cmd: log.append(f"nt{cmd}") or 0), \
             patch.object(ps, "_default_adjust", FakeAdjust(log=log)):
            rec = rel.perform_release("standby", snapshot_fn=iter([report(), report()]).__next__,
                                      preflight_fn=lambda: None)
        self.assertEqual(log, ["enable", "nt4", "disable"])
        self.assertTrue(rec["privilege_scope"]["restored"])


class PreflightAndGuardTests(unittest.TestCase):
    def test_held_disabled_is_ready_with_scoped_enable(self):
        rec = pc.check(query=lambda: [(NAME, 0)])
        self.assertIs(rec["ready"], False)
        self.assertIs(rec["ready_with_scoped_enable"], True)
        self.assertIn("held but disabled; --release enables it only", pc.format_check(rec))

    def test_not_held_is_not_ready(self):
        rec = pc.check(query=lambda: [])
        self.assertIs(rec["ready_with_scoped_enable"], False)
        self.assertIn("not held", pc.format_check(rec))

    def test_ab_guard_accepts_held_disabled_and_blocks_not_held(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ok = abprotocol.check_guards(tmp, lambda: pc.check(query=lambda: [(NAME, 0)]))
            self.assertTrue(ok["ok"], ok["blockers"])
            bad = abprotocol.check_guards(tmp, lambda: pc.check(query=lambda: []))
            self.assertFalse(bad["ok"])
            self.assertIn("0xC0000061", bad["blockers"][0])


if __name__ == "__main__":
    unittest.main()
