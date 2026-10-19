"""The memory window calls working-set release only, and renders honest numbers."""

import sys
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
UI = Path(__file__).resolve().parent.parent / "cpu_memory_cleanup" / "desktop_ui.py"

LEVELS = {
    "available_bytes": 10 * 1024 ** 3,
    "total_bytes": 40 * 1024 ** 3,
    "used_bytes": 30 * 1024 ** 3,
    "commit_used_bytes": 50 * 1024 ** 3,
    "commit_limit_bytes": 80 * 1024 ** 3,
}

RECORD = {
    "cleanup_performed": True,
    "before": {"available_bytes": 4 * 1024 ** 3, "commit_used_bytes": 57 * 1024 ** 3},
    "after": {"available_bytes": 11 * 1024 ** 3, "commit_used_bytes": 57 * 1024 ** 3 + 200 * 1024 ** 2},
    "deltas": {"available_bytes_delta": 7 * 1024 ** 3, "commit_used_bytes_delta": 200 * 1024 ** 2},
    "actions": [{
        "what": "working-set", "ok": True, "error": None,
        "ntstatus_hex": "0x00000000", "ntstatus_name": "STATUS_SUCCESS",
    }],
}


class ViewTests(unittest.TestCase):
    def test_percent_and_result_use_available_and_commit(self):
        from cpu_memory_cleanup.desktop_ui import POLL_MS, format_result, smoothstep, used_percent

        self.assertEqual(POLL_MS, 200)
        self.assertAlmostEqual(used_percent(LEVELS), 75.0)
        self.assertAlmostEqual(used_percent({
            "total_bytes": 100, "available_bytes": 25, "used_bytes": 0,
        }), 75.0)
        self.assertIsNone(used_percent({"total_bytes": 0, "used_bytes": 0}))
        self.assertAlmostEqual(smoothstep(80, 40, 0), 80)
        self.assertAlmostEqual(smoothstep(80, 40, 0.5), 60)
        self.assertAlmostEqual(smoothstep(80, 40, 1), 40)
        text = format_result(RECORD)
        self.assertIn("4.000 GiB", text)
        self.assertIn("11.000 GiB", text)
        self.assertIn("+7.000 GiB", text)
        self.assertIn("Commit", text)
        self.assertIn("working-set OK 0x00000000", text)
        self.assertNotIn("释放没有成功", text)

    def test_failed_release_does_not_claim_success(self):
        from cpu_memory_cleanup.desktop_ui import format_result

        text = format_result({"cleanup_performed": False, "before": {}, "after": {}, "deltas": {}, "actions": []})
        self.assertIn("释放没有成功", text)


class WindowTests(unittest.TestCase):
    def test_button_releases_working_set_only_and_shows_before_after(self):
        import tkinter as tk
        from cpu_memory_cleanup.desktop_ui import EVIDENCE_NOTE, MemoryWindow

        calls = []

        def release_fn(what):
            calls.append(what)
            return RECORD

        root = tk.Tk()
        root.withdraw()
        try:
            clock = {"now": 0.0}
            window = MemoryWindow(
                root, lambda: LEVELS, release_fn, poll_ms=60_000, anim_ms=800,
                clock=lambda: clock["now"],
            )
            window.show_levels(LEVELS)
            self.assertEqual(window.percent.cget("text"), "75%")
            self.assertIn("仅物理内存", window.caption.cget("text"))
            self.assertIn("不是 CPU", window.formula.cget("text"))
            self.assertIn("10.000 GiB", window.available.cget("text"))
            self.assertIn("50.000 GiB", window.commit.cget("text"))
            self.assertIn("cosmetic-or-harmful", window.note.cget("text"))
            self.assertEqual(window.note.cget("text"), EVIDENCE_NOTE)
            lower = dict(LEVELS, available_bytes=30 * 1024 ** 3)
            window.show_levels(lower)
            self.assertEqual(window.percent.cget("text"), "75%")
            self.assertIn("30.000 GiB", window.available.cget("text"))
            clock["now"] = 0.4
            window._anim_frame()
            self.assertEqual(window.percent.cget("text"), "50%")
            fill = window.bar.coords(window.bar_fill)[2]
            self.assertGreater(fill, 0)
            self.assertLess(fill, float(window.bar.cget("width")))
            clock["now"] = 0.8
            window._anim_frame()
            self.assertEqual(window.percent.cget("text"), "25%")
            window.accelerate()
            window.accelerate()
            self.assertEqual(window.button.cget("text"), "正在加速…")
            self.assertEqual(str(window.button.cget("state")), "disabled")
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                window.pump()
                root.update()
                if calls and "4.000 GiB" in window.result.cget("text"):
                    break
                time.sleep(0.02)
            self.assertEqual(calls, ["working-set"])
            self.assertIn("+7.000 GiB", window.result.cget("text"))
            self.assertEqual(window.button.cget("text"), "一键加速")
        finally:
            root.destroy()


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class HostLevelTests(unittest.TestCase):
    def test_host_levels_match_snapshot_physical_and_commit(self):
        from cpu_memory_cleanup.windows import host_levels

        levels = host_levels()
        self.assertGreater(levels["total_bytes"], 0)
        self.assertEqual(levels["used_bytes"] + levels["available_bytes"], levels["total_bytes"])
        self.assertGreater(levels["commit_limit_bytes"], 0)
        self.assertGreaterEqual(levels["commit_used_bytes"], 0)
        self.assertLessEqual(levels["available_bytes"], levels["total_bytes"])


class LauncherTests(unittest.TestCase):
    def test_double_click_launcher_opens_the_gui_and_does_not_release_itself(self):
        text = (SCRIPTS / "desktop-memory-ui.cmd").read_text(encoding="utf-8").lower()
        self.assertIn("pythonw -m cpu_memory_cleanup.desktop_ui", text)
        source = UI.read_text(encoding="utf-8").lower()
        for token in (
            "taskkill", "stop-process", "stop-service", "runas", "reg add", "reg.exe",
            "ntsetsysteminformation", "emptyworkingset", "--release-what standby",
            "--release-what both", "perform_release(\"standby\")", "perform_release(\"both\")",
        ):
            self.assertNotIn(token, text, token)
            self.assertNotIn(token, source, token)
        self.assertIn('release_what = "working-set"', source)
