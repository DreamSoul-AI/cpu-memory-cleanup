from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cpu_memory_cleanup.system import runtime


class RuntimeTests(unittest.TestCase):
    def test_build_fingerprint_includes_nested_product_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "system").mkdir()
            (root / "application").mkdir()
            nested = root / "application" / "core.py"
            nested.write_text("before", encoding="utf-8")
            with patch.object(runtime, "__file__", str(root / "system" / "runtime.py")):
                before = runtime.Runtime().information()["build_id"]
                nested.write_text("after", encoding="utf-8")
                after = runtime.Runtime().information()["build_id"]
        self.assertNotEqual(before, after)

    def test_diagnostic_uses_business_event_without_modifying_it(self):
        event = {"event": "operation_failed", "runtime_id": "run", "message": "safe"}
        with patch.object(runtime.LOGGER, "warning") as output:
            runtime.Runtime().diagnostic(event)
        self.assertEqual(event, {"event": "operation_failed", "runtime_id": "run", "message": "safe"})
        self.assertIn('"runtime_id": "run"', output.call_args.args[0])
