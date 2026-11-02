"""包公共入口的直接调用；全部系统能力为合成实现。"""

import tempfile
import unittest
from unittest.mock import patch

from cpu_memory_cleanup import api
from tests.fixtures import FakeRuntime


class SyntheticMemory:
    def __init__(self):
        self.available = 2 * 1024 ** 3
        self.reads = self.actions = 0

    def read_memory(self):
        self.reads += 1
        return api.Sample("display", 0, "2026-10-30T00:00:00+08:00", {
            "physical": {"total_bytes": 8 * 1024 ** 3, "available_bytes": self.available},
            "commit": {"used_bytes": 4 * 1024 ** 3, "limit_bytes": 16 * 1024 ** 3}})

    def open_memory_observation(self):
        return self

    def read(self, run_id, sequence):
        sample = self.read_memory()
        return api.Sample(run_id, sequence, sample.timestamp, sample.metrics)

    def open_operation(self, request):
        self.request = request
        return self

    def preflight(self):
        return {"supported": True, "privilege_available": True, "lock_acquired": True, "scope": "system"}

    def perform_once(self):
        self.actions += 1
        self.available += 1024 ** 3
        return {"attempted": True, "call_outcome": "success", "status": 0, "privilege_restored": True}

    def close(self):
        return {"closed": True, "errors": []}


class ApiTests(unittest.TestCase):
    def application(self, backend):
        return api.create_application(reader=backend, maintainer=backend, runtime=FakeRuntime(),
                                      capabilities=api.Capabilities("synthetic", "available", ("working-set",)))

    def test_default_factory_only_assembles_inert_shared_backend(self):
        backend = object()
        with patch.object(api, "select_backend", return_value=(backend, api.Capabilities("synthetic"))) as select:
            app = api.create_application()
        self.assertIs(app.reader, backend)
        self.assertIs(app.maintainer, backend)
        self.assertIsInstance(app.records, api.RecordStore)
        select.assert_called_once_with()

    def test_package_memory_journey_returns_objects_and_reuses_original_run(self):
        backend = SyntheticMemory()
        app = self.application(backend)
        status = app.memory_status()
        self.assertIsInstance(status, api.RunReport)
        self.assertEqual(status.completion, "success")
        self.assertEqual(status.samples[0].run_id, status.run_id)
        self.assertEqual(status.samples[0].metrics["physical"]["available_bytes"], 2 * 1024 ** 3)
        self.assertEqual((backend.reads, backend.actions), (1, 0))
        with tempfile.TemporaryDirectory() as directory:
            result = app.memory_cleanup(directory, confirmed=True, confirmation_source="synthetic:explicit-request")
            self.assertEqual(result.completion, "success", result.errors)
            self.assertEqual(result.action["call_outcome"], "success")
            self.assertEqual(result.metadata["header"]["observation_scope"], "memory-only")
            self.assertEqual([s.metrics["physical"]["available_bytes"] for s in result.samples],
                             [2 * 1024 ** 3, 3 * 1024 ** 3])
            app.shutdown()
            with patch.object(api, "select_backend", side_effect=AssertionError("回看不能选择实时接入")):
                replay = api.create_application(offline=True).memory_result(result.output_dir)
        self.assertEqual(replay.completion, "success", replay.errors)
        self.assertEqual(replay.action, result.action)
        self.assertEqual(replay.metadata["header"]["run_id"], result.run_id)
        self.assertEqual(replay.samples[0].run_id, result.run_id)
        self.assertEqual((backend.reads, backend.actions), (3, 1))

    def test_cleanup_without_confirmation_never_reads_or_maintains(self):
        backend = SyntheticMemory()
        app = self.application(backend)
        with tempfile.TemporaryDirectory() as directory:
            result = app.memory_cleanup(directory)
        self.assertEqual(result.completion, "failed")
        self.assertFalse(result.action["attempted"])
        self.assertEqual((backend.reads, backend.actions), (0, 0))

    def test_offline_creation_and_status_do_not_connect_to_live_backend(self):
        with patch.object(api, "select_backend", side_effect=AssertionError("离线不能选择实时接入")):
            app = api.create_application(offline=True, runtime=FakeRuntime())
            report = app.memory_status()
        self.assertEqual(report.completion, "failed")
        self.assertEqual(report.samples, [])
        self.assertFalse(report.action["attempted"])
