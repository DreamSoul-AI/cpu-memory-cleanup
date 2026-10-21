"""装配测试只注入假应用和假平台对象。"""

import ast
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from cpu_memory_cleanup import __main__ as entry
from cpu_memory_cleanup.contracts import Capabilities, RunReport


class MainTests(unittest.TestCase):
    def test_default_assembly_uses_one_shared_inert_backend(self):
        backend = object()
        app = Mock()
        app.inspect.return_value = RunReport("run", "inspect", completion="success")
        with patch.object(entry, "select_backend", return_value=(backend, Capabilities("fake", "available", ("working-set",)))) as factory, \
             patch.object(entry, "Application", return_value=app) as compose, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.main([]), 0)
        factory.assert_called_once_with()
        self.assertIs(compose.call_args.kwargs["reader"], backend)
        self.assertIs(compose.call_args.kwargs["maintainer"], backend)
        self.assertIsInstance(compose.call_args.kwargs["records"], entry.RecordStore)
        self.assertIsInstance(compose.call_args.kwargs["runtime"], entry.Runtime)
        app.inspect.assert_called_once()
        app.maintain.assert_not_called()

    def test_offline_injected_application_opens_no_backend(self):
        app = Mock()
        app.review.return_value = RunReport("run", "review", completion="success")
        with patch.object(entry, "select_backend") as backend, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.main(["--review", "历史运行"], app=app), 0)
        backend.assert_not_called()
        app.review.assert_called_once_with(Path("历史运行"), comparison=None)
        app.inspect.assert_not_called()

    def test_invalid_maintenance_is_rejected_before_assembly(self):
        with patch.object(entry, "select_backend") as backend, contextlib.redirect_stderr(io.StringIO()), \
             self.assertRaises(SystemExit):
            entry.main(["--release", "--release-what", "both", "--output-dir", "out"])
        backend.assert_not_called()

    def test_gui_selects_desktop_without_loading_real_tk(self):
        app = Mock()
        with patch("cpu_memory_cleanup.interaction.desktop.run", return_value=0) as desktop:
            self.assertEqual(entry.main(["--gui"], app=app), 0)
        desktop.assert_called_once_with(app, {"output_dir": None})
        app.inspect.assert_not_called()

    def test_gui_import_is_inside_main_only(self):
        tree = ast.parse(Path(entry.__file__).read_text(encoding="utf-8"))
        module_imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertFalse(any("desktop" in ast.unparse(node) or "tkinter" in ast.unparse(node) for node in module_imports))

    def test_offline_default_assembly_never_selects_a_platform_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "历史.jsonl"
            source.write_text('{"warnings": []}\n', encoding="utf-8")
            output = io.StringIO()
            with patch.object(entry, "select_backend", side_effect=AssertionError("离线不能选择实时接入")), \
                 contextlib.redirect_stdout(output):
                code = entry.main(["--review", str(source), "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["completion"], "partial")
        self.assertEqual(json.loads(output.getvalue())["mode"], "review")

    def test_unsupported_system_default_has_no_native_read_or_maintenance(self):
        capabilities = Capabilities("unimplemented", reason="没有实时接入")
        output = io.StringIO()
        with patch.object(entry, "select_backend", return_value=(None, capabilities)), contextlib.redirect_stdout(output):
            code = entry.main(["--json"])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result["samples"], [])
        self.assertFalse(result["action"]["attempted"])
