import ast
from importlib.util import resolve_name
from pathlib import Path
import unittest

import cpu_memory_cleanup


class ImportTests(unittest.TestCase):
    def test_product_import_resolves_to_src_package(self):
        root = Path(__file__).resolve().parents[3]
        self.assertEqual(Path(cpu_memory_cleanup.__file__).resolve(), root / "src" / "cpu_memory_cleanup" / "__init__.py")

    def test_package_import_has_no_backend_or_gui_import(self):
        path = Path(cpu_memory_cleanup.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertEqual(imports, [])

    def test_dependency_direction_excludes_concrete_backend_from_core(self):
        folder = Path(cpu_memory_cleanup.__file__).parent
        banned = {"core": {"windows", "cli", "desktop", "tkinter", "ctypes", "system", "pathlib", "os", "platform", "logging", "hashlib"},
                  "rules": {"core", "windows", "cli", "desktop", "tkinter", "ctypes", "pathlib", "time"},
                  "contracts": {"core", "rules", "windows", "cli", "desktop", "tkinter", "ctypes"}}
        for name, forbidden in banned.items():
            location = "application/" + name if name in ("core", "rules") else name
            tree = ast.parse((folder / f"{location}.py").read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    modules = {node.module.split(".")[0]} if node.module else {alias.name for alias in node.names}
                else:
                    continue
                self.assertFalse(modules & forbidden, f"{name}: {modules & forbidden}")

    def test_layer_packages_keep_dependency_direction(self):
        root = Path(cpu_memory_cleanup.__file__).parent
        allowed = {"interaction": {"interaction", "application", "contracts"},
                   "application": {"application", "contracts"}, "system": {"system", "contracts"}}
        for layer, permitted in allowed.items():
            for path in (root / layer).rglob("*.py"):
                package = "cpu_memory_cleanup." + ".".join(path.relative_to(root).parts[:-1])
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
                    modules = []
                    if isinstance(node, ast.ImportFrom):
                        base = resolve_name("." * node.level + (node.module or ""), package) if node.level else node.module
                        modules = [base] if node.module else [base + "." + n.name for n in node.names]
                    elif isinstance(node, ast.Import):
                        modules = [n.name for n in node.names]
                    for module in modules:
                        if module.startswith("cpu_memory_cleanup."):
                            self.assertIn(module.split(".")[1], permitted, str(path))
