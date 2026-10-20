"""启动脚本静态检查，不启动桌面。"""

from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "desktop-memory-ui.cmd"


class DesktopMemoryUiTests(unittest.TestCase):
    def test_gui_uses_composition_entry_and_forwards_exit_code(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("python -m cpu_memory_cleanup --gui %*", text)
        self.assertIn("exit /b %ERRORLEVEL%", text)
        self.assertIn('cd /d "%~dp0.."', text)
        self.assertIn("setlocal", text)
        self.assertIn('set "PYTHONPATH=%CD%\\src;%PYTHONPATH%"', text)
        self.assertIn('set "PYTHONPATH=%CD%\\src"', text)
        self.assertLess(text.index('set "PYTHONPATH='), text.index("python -m cpu_memory_cleanup"))
        self.assertNotIn("desktop_ui", text)
        self.assertNotIn("--release", text)
