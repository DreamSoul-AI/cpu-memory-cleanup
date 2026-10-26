"""启动脚本静态检查，不启动桌面。"""

from pathlib import Path
import unittest
from importlib.machinery import SourceFileLoader
import importlib.util
from unittest.mock import Mock, patch
import sys
import types

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "desktop-memory-ui.cmd"


class DesktopMemoryUiTests(unittest.TestCase):
    def windowed_entry(self):
        path = SCRIPT.with_suffix(".pyw")
        spec = importlib.util.spec_from_loader("windowed_memory_entry", SourceFileLoader("windowed_memory_entry", str(path)))
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        return entry

    def test_windowed_entry_dispatches_gui_without_default_sampling(self):
        from cpu_memory_cleanup import __main__ as entry
        with patch.object(entry, "main", return_value=0) as start:
            self.assertEqual(self.windowed_entry().main(["--output-dir", "chosen"]), 0)
        start.assert_called_once_with(["--gui", "--output-dir", "chosen"])

    def test_windowed_entry_reports_startup_error_without_console(self):
        from cpu_memory_cleanup import __main__ as entry
        popup = Mock()
        tk = types.SimpleNamespace(messagebox=types.SimpleNamespace(showerror=popup))
        with patch.object(entry, "main", side_effect=RuntimeError("controlled startup failure")), patch.dict(sys.modules, {"tkinter": tk}):
            self.assertEqual(self.windowed_entry().main([]), 1)
        self.assertIn("controlled startup failure", popup.call_args.args[1])

    def test_gui_uses_composition_entry_and_forwards_exit_code(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('start "" pythonw "%~dp0desktop-memory-ui.pyw" %*', text)
        self.assertIn("exit /b %ERRORLEVEL%", text)
        self.assertIn('cd /d "%~dp0.."', text)
        self.assertIn("setlocal", text)
        self.assertIn('set "PYTHONPATH=%CD%\\src;%PYTHONPATH%"', text)
        self.assertIn('set "PYTHONPATH=%CD%\\src"', text)
        self.assertLess(text.index('set "PYTHONPATH='), text.index('start "" pythonw "%~dp0desktop-memory-ui.pyw"'))
        self.assertNotIn("desktop_ui", text)
        self.assertNotIn("--release", text)
