import tempfile
from pathlib import Path
import unittest
from cpu_memory_cleanup.application.settings import validate_preferences
from cpu_memory_cleanup.system.preferences import Preferences


class PreferenceTests(unittest.TestCase):
    def test_saved_protection_restores_but_does_not_arm_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Preferences(Path(directory) / "prefs.json", validator=validate_preferences)
            value = store.load()
            value["protections"] = [{"image_path": r"C:\App\x.exe"}]
            value["rule"] = {"mode": "pressure"}
            store.save(value)
            loaded = store.load()
            self.assertEqual(loaded["protections"], [{"image_path": r"c:\app\x.exe"}])
            self.assertNotIn("armed", loaded)
            self.assertEqual(loaded["rule"]["mode"], "pressure")

    def test_invalid_preferences_do_not_overwrite_valid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Preferences(Path(directory) / "prefs.json", validator=validate_preferences)
            store.save(store.load())
            before = store.path.read_bytes()
            with self.assertRaises(ValueError):
                store.save({"schema_version": 1, "resident": "yes"})
            self.assertEqual(store.path.read_bytes(), before)
