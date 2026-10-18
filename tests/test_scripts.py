"""Static safety checks for helper scripts under scripts/ (read-only only)."""

import re
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

FORBIDDEN = (
    "Stop-Process", "Stop-Service", "Set-Service", "Restart-Service",
    "Suspend-Service", "taskkill", "EmptyWorkingSet", "EmptyStandbyList",
    "SetProcessWorkingSetSize", "SetSystemFileCacheSize",
    "NtSetSystemInformation", "Invoke-CimMethod", "Invoke-WmiMethod",
    "Remove-CimInstance", "Set-CimInstance", "Set-WmiInstance", ".Terminate(",
    ".Kill(", "RunAs", "Set-ItemProperty", "wmic", "Start-Process",
)


class TreeSeriesScriptTests(unittest.TestCase):
    def setUp(self):
        self.path = SCRIPTS / "tree-series.ps1"
        self.text = self.path.read_text(encoding="utf-8")

    def test_no_mutating_or_elevating_calls(self):
        lowered = self.text.lower()
        for token in FORBIDDEN:
            self.assertNotIn(token.lower(), lowered, token)

    def test_bounded_parameters_are_mandatory(self):
        for name, low, high in (("IntervalSeconds", 5, 3600), ("Count", 1, 1440)):
            pattern = (
                r"\[Parameter\(Mandatory = \$true\)\]\[ValidateRange\(%d, %d\)\]\[int\]\$%s"
                % (low, high, name)
            )
            self.assertRegex(self.text, pattern)

    def test_only_reads_cim(self):
        classes = set(re.findall(r"Get-CimInstance -ClassName (\w+)", self.text))
        self.assertEqual(classes, {"Win32_Process", "Win32_OperatingSystem"})

    def test_record_has_no_pid_or_command_line_fields(self):
        record = self.text[self.text.index("$rec = [ordered]@{"):self.text.index("$line = $rec")]
        for field in ("pid", "process_id", "command_line", "commandline", "path"):
            self.assertIsNone(re.search(r"\b%s\s*=" % field, record, re.I), field)


if __name__ == "__main__":
    unittest.main()