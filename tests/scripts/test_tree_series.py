"""PowerShell 兼容入口只检查源码，不调用系统采样。"""

from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "tree-series.ps1"


class TreeSeriesTests(unittest.TestCase):
    def test_parameters_forward_to_bounded_python_use_case(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("& python -m cpu_memory_cleanup --sample --interval $IntervalSeconds --count $Count --jsonl $outputPath --with-processes --top $TopOwners", text)
        self.assertIn("[ValidateRange(1, 1000)][int]$Count", text)
        self.assertIn("[IO.Path]::GetFullPath($JsonlPath)", text)
        self.assertIn("$sourcePath = Join-Path $projectRoot 'src'", text)
        self.assertIn("Push-Location -LiteralPath $projectRoot", text)
        self.assertIn("$previousPythonPath = $env:PYTHONPATH", text)
        self.assertIn("$sourcePath + [IO.Path]::PathSeparator + $previousPythonPath", text)
        finally_body = text.split("} finally {", 1)[1]
        self.assertIn("$env:PYTHONPATH = $previousPythonPath", finally_body)
        self.assertIn("Pop-Location", finally_body)
        self.assertLess(text.index("$env:PYTHONPATH = if"), text.index("& python -m cpu_memory_cleanup"))
        self.assertIn("exit $resultCode", text)

    def test_script_has_no_duplicate_collection_or_maintenance(self):
        text = SCRIPT.read_text(encoding="utf-8").lower()
        for call in ("get-ciminstance", "get-counter", "start-sleep", "ntsetsysteminformation", "--release", "stop-process", "runas"):
            self.assertNotIn(call, text)
