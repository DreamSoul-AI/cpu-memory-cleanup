"""Static safety checks for helper scripts under scripts/.

tree-series.ps1 stays read-only. desktop-memory-button.cmd's default
double-click is a read-only report; working-set release needs the explicit
argument release. It still must not kill, elevate, or edit settings.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
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

    def test_only_reads_cim_and_optional_pdh(self):
        classes = set(re.findall(r"Get-CimInstance -ClassName (\w+)", self.text))
        self.assertEqual(classes, {"Win32_Process", "Win32_OperatingSystem"})
        # Get-Counter is read-only PDH; required for residual fields, fail-soft in script.
        self.assertIn("Get-Counter", self.text)
        self.assertIn("commit_residual", self.text)
        for field in (
            "process_private_gib", "paged_pool_gib", "nonpaged_pool_gib",
            "system_driver_gib", "residual_gib", "residual_pct_of_commit",
        ):
            self.assertIn(field, self.text)

    def test_record_has_no_pid_or_command_line_fields(self):
        record = self.text[self.text.index("$rec = [ordered]@{"):self.text.index("$line = $rec")]
        for field in ("pid", "process_id", "command_line", "commandline", "path"):
            self.assertIsNone(re.search(r"\b%s\s*=" % field, record, re.I), field)


    def test_age_buckets_are_aggregates_only(self):
        for bucket in ("lt10m", "10to60m", "1to3h", "gt3h", "unknown"):
            self.assertIn("'%s'" % bucket, self.text)
        for kind in ("server_mjs", "cua_repl", "node_repl"):
            self.assertIn("'%s'" % kind, self.text)
        self.assertRegex(self.text, r"\$minutes -lt 10\)")
        self.assertRegex(self.text, r"\$minutes -lt 60\)")
        self.assertRegex(self.text, r"\$minutes -lt 180\)")
        record = self.text[self.text.index("$rec = [ordered]@{"):self.text.index("$line = $rec")]
        self.assertIn("age_buckets = @($ageRows)", record)


@unittest.skipUnless(sys.platform == "win32" and shutil.which("powershell"), "Windows only")
class TreeSeriesSmokeTest(unittest.TestCase):
    """Runs one bounded read-only sample (CIM queries only) into a temp file."""

    def test_single_sample_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "series.jsonl"
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(SCRIPTS / "tree-series.ps1"), "-IntervalSeconds", "5",
                 "-Count", "1", "-JsonlPath", str(out)],
                check=True, timeout=120, capture_output=True,
            )
            lines = out.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["kind"], "tree-series")
        self.assertIsNone(rec["born_since_prev"])
        rows = rec["codex_tree"]["age_buckets"]
        self.assertEqual([r["kind"] for r in rows], ["server_mjs", "cua_repl", "node_repl"])
        for row in rows:
            for bucket in ("lt10m", "10to60m", "1to3h", "gt3h", "unknown"):
                self.assertGreaterEqual(row[bucket]["processes"], 0)
        by_kind = {r["kind"]: r for r in rows}
        tree = rec["codex_tree"]
        self.assertEqual(
            sum(by_kind["server_mjs"][b]["processes"] for b in ("lt10m", "10to60m", "1to3h", "gt3h", "unknown")),
            tree["server_mjs"],
        )
        self.assertEqual(
            sum(by_kind["cua_repl"][b]["processes"] for b in ("lt10m", "10to60m", "1to3h", "gt3h", "unknown")),
            tree["cua_repl"],
        )
        text = lines[0].lower()
        for field in ("\"pid\"", "\"process_id\"", "\"command_line\"", "\"commandline\""):
            self.assertNotIn(field, text)
        # Commit residual block: present; numeric or null (fail-soft on PDH).
        self.assertIn("commit_residual", rec)
        cr = rec["commit_residual"]
        for key in (
            "process_private_gib", "paged_pool_gib", "nonpaged_pool_gib",
            "system_driver_gib", "residual_gib", "residual_pct_of_commit",
        ):
            self.assertIn(key, cr)
            val = cr[key]
            self.assertTrue(val is None or isinstance(val, (int, float)), key)
        # process_private_gib falls back to CIM private sum, so should be a number.
        self.assertIsInstance(cr["process_private_gib"], (int, float))
        self.assertGreaterEqual(cr["process_private_gib"], 0)
        if cr["residual_gib"] is not None:
            host = rec["host"]
            expected = (
                host["commit_used_gib"]
                - cr["process_private_gib"]
                - cr["paged_pool_gib"]
                - cr["nonpaged_pool_gib"]
                - cr["system_driver_gib"]
            )
            self.assertAlmostEqual(cr["residual_gib"], expected, places=2)


class MemoryButtonTests(unittest.TestCase):
    def test_double_click_is_read_only_until_release_argument(self):
        text = (SCRIPTS / "desktop-memory-button.cmd").read_text(encoding="utf-8").replace("\r\n", "\n")
        lowered = text.lower()
        head, sep, tail = lowered.partition(":release\n")
        self.assertEqual(sep, ":release\n")
        self.assertIn('if /i "%~1"=="release" goto release', head)
        self.assertNotIn("--release", head)
        self.assertIn("python -m cpu_memory_cleanup\n", head)
        self.assertIn("does not release memory", head)
        self.assertIn("python -m cpu_memory_cleanup --release --release-what working-set\n", tail)
        self.assertNotIn("--release-what standby", lowered)
        self.assertNotIn("--release-what both", lowered)
        for token in (
            "taskkill", "stop-process", "stop-service", "ntsetsysteminformation",
            "runas", "reg add", "reg.exe", "pagefile", "emptyworkingset", "set /p",
        ):
            self.assertNotIn(token, lowered, token)
        self.assertIn("cosmetic-or-harmful", text)
        self.assertIn("+0.24/+0.30", text)


if __name__ == "__main__":
    unittest.main()