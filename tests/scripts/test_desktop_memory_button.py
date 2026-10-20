"""兼容按钮静态检查，不执行批处理。"""

from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "desktop-memory-button.cmd"


class DesktopMemoryButtonTests(unittest.TestCase):
    def test_default_route_is_readonly(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('if "%~1"=="" goto readonly', text)
        self.assertIn('cd /d "%~dp0.."', text)
        self.assertIn("setlocal", text)
        self.assertIn('set "PYTHONPATH=%CD%\\src;%PYTHONPATH%"', text)
        self.assertIn('set "PYTHONPATH=%CD%\\src"', text)
        self.assertLess(text.index('set "PYTHONPATH='), text.index('if "%~1"=="" goto readonly'))
        readonly = text.split(":readonly\n", 1)[1].split(":release\n", 1)[0]
        self.assertIn("python -m cpu_memory_cleanup\n", readonly)
        self.assertNotIn("--release", readonly)

    def test_release_requires_directory_and_single_mechanism(self):
        text = SCRIPT.read_text(encoding="utf-8")
        release = text.split(":release\n", 1)[1].split(":usage\n", 1)[0]
        self.assertIn('if "%~2"=="" goto usage', release)
        self.assertIn('if not "%~3"=="" goto usage', release)
        self.assertIn('--release --release-what working-set --output-dir "%~2"', release)
        self.assertIn("exit /b %ERRORLEVEL%", release)
        self.assertNotIn("--release-what both", text)
