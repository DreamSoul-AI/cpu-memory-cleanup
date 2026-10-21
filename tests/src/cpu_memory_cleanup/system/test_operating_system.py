import sys
import types
import unittest
from unittest.mock import Mock, patch

from cpu_memory_cleanup.system.operating_system import select_backend


class OperatingSystemTests(unittest.TestCase):
    def test_unsupported_system_does_not_load_windows_or_offer_maintenance(self):
        name = "cpu_memory_cleanup.system.platforms.windows"
        with patch.dict(sys.modules, {name: None}):
            backend, capabilities = select_backend("unimplemented-os")
        self.assertIsNone(backend)
        self.assertEqual(capabilities.reading, "unsupported")
        self.assertEqual(capabilities.mechanisms, ())
        self.assertIn("离线", capabilities.reason)

    def test_supported_selection_only_constructs_an_inert_backend(self):
        factory = Mock(return_value=object())
        fake = types.SimpleNamespace(WindowsBackend=factory)
        with patch.dict(sys.modules, {"cpu_memory_cleanup.system.platforms.windows": fake}):
            backend, capabilities = select_backend("win32")
        self.assertIs(backend, factory.return_value)
        self.assertEqual(capabilities.mechanisms, ("working-set", "standby"))
        factory.assert_called_once_with()
