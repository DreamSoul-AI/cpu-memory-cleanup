"""真实回环HTTP→Package→合成后端旅程，不读取主机资源。"""

import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cpu_memory_cleanup.api import create_application, Capabilities
from cpu_memory_cleanup.interaction.web import LocalWebServer
from tests.fixtures import ScenarioBackend, FakeRuntime


class WebTests(unittest.TestCase):
    def setUp(self):
        import ctypes
        guard = patch.object(ctypes, "WinDLL", side_effect=AssertionError("禁止真实DLL"), create=True)
        guard.start()
        self.addCleanup(guard.stop)
        guard = patch("cpu_memory_cleanup.api.select_backend", side_effect=AssertionError("禁止真实装配"))
        guard.start()
        self.addCleanup(guard.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = ScenarioBackend()
        self.reads = []
        original = self.backend.read_memory
        def read():
            self.reads.append("memory")
            return original()
        self.backend.read_memory = read
        self.app = create_application(reader=self.backend, maintainer=self.backend, runtime=FakeRuntime(),
            capabilities=Capabilities("synthetic", "available", ("working-set",), scopes=("system", "process")))
        self.app._sleep = lambda seconds: None
        self.server = LocalWebServer(self.app, Path(self.temp.name) / "runs", preview=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.stop()
        self.thread.join(2)
        self.server.server_close()
        self.assertFalse(self.thread.is_alive())

    def request(self, method, path, value=None, *, token=True, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        actual = {"Content-Type": "application/json"}
        if token:
            actual["X-Session-Token"] = self.server.token
        actual.update(headers or {})
        connection.request(method, path, body=None if value is None else json.dumps(value), headers=actual)
        response = connection.getresponse()
        data = response.read()
        status = response.status
        mime = response.getheader("Content-Type")
        connection.close()
        return status, json.loads(data) if mime.startswith("application/json") else data

    def action(self, value):
        status, job = self.request("POST", "/api/action", value)
        self.assertEqual(status, 202, job)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status, result = self.request("GET", "/api/job?id=" + job["id"])
            self.assertEqual(status, 200)
            if result["state"] != "running":
                self.assertEqual(result["state"], "finished", result)
                return result
            time.sleep(0.005)
        self.fail("合成任务未完成")

    def test_opening_page_assets_and_session_never_reads_backend(self):
        for path in ("/", "/app.js", "/cleanup-effect.js", "/styles.css", "/api/session"):
            status, content = self.request("GET", path, token=False)
            self.assertEqual(status, 200)
        self.assertEqual(self.reads, [])
        self.assertEqual(self.backend.actions, [])
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_formal_factory_selects_real_backend_path_without_reading_or_gui(self):
        from cpu_memory_cleanup.api import create_web_server
        capabilities = Capabilities("win32", "available", ("working-set", "standby"), scopes=("system", "process"))
        with patch("cpu_memory_cleanup.api.select_backend", return_value=(self.backend, capabilities)) as select:
            server = create_web_server(output_dir=Path(self.temp.name) / "formal" / "runs", port=0)
            try:
                select.assert_called_once_with()
                self.assertIs(server.app.reader, self.backend)
                self.assertIs(server.app.maintainer, self.backend)
                self.assertFalse(server.preview)
                self.assertEqual(self.reads, [])
                self.assertEqual(self.backend.actions, [])
                self.assertFalse(server.policy.armed)
            finally:
                server.server_close()
                server.app.shutdown()

    def test_formal_working_set_job_uses_default_process_sweep(self):
        from cpu_memory_cleanup.api import create_web_server
        from cpu_memory_cleanup.system.operating_system import select_backend
        from cpu_memory_cleanup.system.platforms import windows
        from ..system.platforms.test_windows import FakeSystem, Function
        fake, calls = FakeSystem(), []
        fake.kernel.SetProcessWorkingSetSize = Function(lambda handle, *_: calls.append(handle) or True)
        with patch.object(windows, "_load_dll", side_effect=fake.load), \
             patch.object(windows, "_maintenance_lock", threading.Lock()), \
             patch("cpu_memory_cleanup.api.select_backend", side_effect=lambda: select_backend("win32")):
            formal = create_web_server(output_dir=Path(self.temp.name) / "sweep" / "runs", port=0)
            try:
                self.assertFalse(formal.preview)
                self.assertEqual(fake.loads, [])
                before = formal.display()
                job = formal.submit({"kind": "cleanup", "mechanism": "working-set", "confirmed": True})
                formal.worker.join(2)
                self.assertFalse(formal.worker.is_alive())
                result = formal.job(job["id"])
                self.assertEqual(result["report"]["completion"], "success", result)
                self.assertEqual(calls, [11, 12])
                self.assertEqual(result["report"]["action"]["native_method"], "SetProcessWorkingSetSize")
                self.assertIn("成功2、跳过1", result["presentation"]["note"])
                self.assertTrue(formal.display())
                self.assertTrue(before)
                self.assertFalse(formal.policy.armed)
                self.assertEqual(fake.nt_calls, [])
                self.assertEqual(fake.adjustments, [])
            finally:
                formal.server_close()
                formal.app.shutdown()

    def test_web_cli_routes_to_formal_factory_without_sampling(self):
        from cpu_memory_cleanup.__main__ import main
        from cpu_memory_cleanup.interaction.cli import parse_args
        with patch("cpu_memory_cleanup.api.create_web_server", return_value="injected-server") as factory, \
             patch("cpu_memory_cleanup.interaction.web.serve", return_value=0) as serve:
            self.assertEqual(main(["--web", "--port", "8767"], app=self.app), 0)
            factory.assert_called_once_with(app=self.app, output_dir=None, port=8767)
            serve.assert_called_once_with("injected-server")
        self.assertEqual(self.reads, [])
        with self.assertRaises(SystemExit):
            parse_args(["--web", "--count", "1"])

    def test_formal_assembly_reaches_windows_adapter_with_only_injected_dlls(self):
        from cpu_memory_cleanup.api import create_web_server
        from cpu_memory_cleanup.system.platforms.windows import WindowsBackend
        from ..system.platforms.test_windows import FakeSystem
        fake = FakeSystem()
        backend = WindowsBackend(fake.load, now=FakeRuntime().now)
        capabilities = Capabilities("win32", "available", ("working-set",), scopes=("system", "process"))
        with patch("cpu_memory_cleanup.api.select_backend", return_value=(backend, capabilities)):
            formal = create_web_server(output_dir=Path(self.temp.name)/"native"/"runs", port=0)
            try:
                self.assertEqual(fake.loads, [])
                submitted = formal.submit({"kind":"status"})
                formal.worker.join(2)
                self.assertEqual(formal.job(submitted["id"])["report"]["completion"], "success")
                self.assertTrue(fake.loads)
                self.assertEqual(fake.nt_calls, [])
                self.assertEqual(fake.adjustments, [])
            finally:
                formal.server_close()
                formal.app.shutdown()

    def test_preferences_reload_keeps_protection_but_rule_stays_disarmed(self):
        store = self.app.preferences_factory(Path(self.temp.name)/"prefs.json")
        first = LocalWebServer(self.app, self.temp.name, preview=True, preferences=store)
        try:
            value = dict(first.settings)
            value["protections"] = [{"image_path": r"C:\Demo\Meeting\meeting.exe"}]
            first.save_settings(value)
        finally:
            first.server_close()
        second = LocalWebServer(self.app, self.temp.name, preview=True, preferences=store)
        try:
            self.assertEqual(len(second.settings["protections"]), 1)
            self.assertFalse(second.policy.armed)
        finally:
            second.server_close()

    def test_protection_saved_then_applied_to_selected_batch(self):
        self.action({"kind": "targets"})
        settings = self.server.settings.copy()
        settings["protections"] = [{"image_path": r"C:\Demo\Meeting\meeting.exe"}]
        self.assertEqual(self.request("POST", "/api/settings", settings)[0], 200)
        job = self.action({"kind": "batch", "targets": ["201:100201", "101:100101"], "confirmed": True})
        self.assertEqual([i["status"] for i in job["report"]["metadata"]["items"]], ["skipped", "success"])
        self.assertEqual(self.backend.actions, [{"scope": "process", "pid": 101}])

    def test_interval_rule_dispatches_package_batch_and_stop_prevents_repeat(self):
        self.action({"kind": "targets"})
        now = [100.0]
        self.server.clock = lambda: now[0]
        state = self.server.configure_rule({"enabled": True, "confirmed": True, "targets": ["101:100101"],
            "rule": {"mode": "interval", "interval_s": 2, "cooldown_s": 2}})
        self.assertTrue(state["rule"]["armed"])
        now[0] = 102
        self.server.service_actions()
        self.server.worker.join(2)
        self.assertEqual(self.backend.actions, [{"scope": "process", "pid": 101}])
        self.server.configure_rule({"enabled": False})
        now[0] = 110
        self.server.service_actions()
        self.assertEqual(len(self.backend.actions), 1)

    def test_pressure_rule_observes_only_after_arm_and_needs_sustain(self):
        self.action({"kind": "targets"})
        now = [100.0]
        self.server.clock = lambda: now[0]
        self.server.service_actions()
        self.assertEqual(self.reads, [])
        self.backend.available = 1024**3
        self.server.configure_rule({"enabled": True, "confirmed": True, "targets": ["101:100101"],
            "rule": {"mode": "pressure", "available_pct": 10, "sustain_s": 2, "cooldown_s": 1}})
        for instant in (100, 100.2, 101, 101.2, 102, 102.2):
            now[0] = instant
            self.server.service_actions()
            if self.server.worker:
                self.server.worker.join(2)
        self.assertEqual(self.backend.actions, [{"scope": "process", "pid": 101}])
        result_id = self.server.state()["rule"]["last_job"]
        self.assertEqual(self.server.job(result_id)["kind"], "batch")
        now[0] = 103
        self.server.service_actions()
        self.server.worker.join(2)
        self.assertEqual(self.server.state()["rule"]["last_job"], result_id)
        self.server.configure_rule({"enabled": False})

    def test_stop_between_rule_decision_and_submission_blocks_dispatch(self):
        self.action({"kind": "targets"})
        now = [100.0]
        self.server.clock = lambda: now[0]
        self.server.configure_rule({"enabled": True, "confirmed": True, "targets": ["101:100101"],
            "rule": {"mode": "interval", "interval_s": 1, "cooldown_s": 1}})
        original = self.server.submit
        def stop_then_submit(value, **options):
            self.server.configure_rule({"enabled": False})
            return original(value, **options)
        self.server.submit = stop_then_submit
        now[0] = 102
        self.server.service_actions()
        self.assertFalse(self.server.policy.armed)
        self.assertEqual(self.backend.actions, [])

    def test_http_selected_batch_and_saved_result_do_not_repeat_actions(self):
        targets = self.action({"kind": "targets"})["report"]["samples"][0]["processes"]
        job = self.action({"kind": "batch", "targets": [f"{targets[0]['pid']}:{targets[0]['created_at']}"], "confirmed": True})
        self.assertEqual(job["report"]["completion"], "success", job)
        self.assertEqual(self.backend.actions, [{"scope": "process", "pid": 101}])
        before = list(self.reads)
        replay = self.action({"kind": "replay", "source_job": job["id"]})
        self.assertEqual(replay["report"]["completion"], "success", replay)
        self.assertEqual(self.reads, before)
        self.assertEqual(len(self.backend.actions), 1)
        original = next(row for row in self.backend.rows if row["pid"] == 201)
        self.assertEqual(original["working_set_bytes"], 420 * 1024**2)

    def test_global_button_uses_package_once_and_status_is_separate(self):
        self.action({"kind": "status"})
        self.assertEqual(self.backend.actions, [])
        job = self.action({"kind": "cleanup", "confirmed": True})
        self.assertEqual(job["report"]["completion"], "success", job)
        metadata = job["report"]["metadata"]
        self.assertGreaterEqual(metadata["web_timing"]["accepted_to_report_ms"], 0)
        self.assertIsNone(metadata["web_timing"]["ui_render_ms"])
        self.assertIsNone(metadata["diagnostics"]["verification"]["native_call_ms"])
        self.assertEqual(len(job["report"]["samples"]), 2)
        self.assertNotIn("post_observation_seconds", job["report"]["metadata"]["header"])
        self.request("GET", "/api/job?id=" + job["id"])
        self.assertEqual(self.backend.actions, [{"scope": "system", "pid": None}])

    def test_live_display_keeps_reading_during_cleanup_without_dispatching_more_actions(self):
        entered, release = threading.Event(), threading.Event()
        original = self.app.memory_cleanup
        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("合成测试释放超时")
            return original(*args, **kwargs)
        self.app.memory_cleanup = delayed
        try:
            status, job = self.request("POST", "/api/action", {"kind":"cleanup", "confirmed":True})
            self.assertEqual(status, 202)
            self.assertTrue(entered.wait(1))
            for _ in range(2):
                status, display = self.request("POST", "/api/display", {})
                self.assertEqual(status, 200)
                self.assertEqual(display["completion"], "success")
            self.assertEqual(len(self.reads), 2)
            self.assertEqual(self.backend.actions, [])
            self.assertEqual(list(self.server.jobs), [job["id"]])
        finally:
            release.set()
            self.server.worker.join(2)
        result = self.server.job(job["id"])["report"]
        self.assertEqual(result["completion"], "success")
        self.request("POST", "/api/display", {})
        self.assertEqual(self.server.job(job["id"])["report"], result)
        self.assertEqual(self.backend.actions, [{"scope":"system", "pid":None}])

    def test_live_display_requires_session_and_rejects_after_stop(self):
        self.assertEqual(self.request("POST", "/api/display", {}, token=False)[0], 403)
        self.assertEqual(self.reads, [])
        self.server.stopping = True
        self.assertEqual(self.request("POST", "/api/display", {})[0], 409)
        self.assertEqual(self.reads, [])

    def test_cross_origin_no_session_unconfirmed_and_arbitrary_path_are_rejected(self):
        self.assertEqual(self.request("POST", "/api/action", {"kind": "cleanup", "confirmed": True}, token=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/action", {"kind": "status"}, headers={"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request("GET", "/api/session", headers={"Host": "example.com"})[0], 403)
        self.assertEqual(self.request("POST", "/api/action", {"kind": "cleanup"})[0], 400)
        self.assertEqual(self.request("POST", "/api/action", {"kind": "replay", "source_job": "../../secret"})[0], 400)
        self.assertEqual(self.request("GET", "/../../pyproject.toml")[0], 404)
        self.assertEqual(self.backend.actions, [])

    def test_single_flight_cancel_and_explicit_shutdown_wait_for_worker(self):
        entered, release = threading.Event(), threading.Event()
        original = self.app.memory_cleanup
        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("合成测试释放超时")
            return original(*args, **kwargs)
        self.app.memory_cleanup = delayed
        status, job = self.request("POST", "/api/action", {"kind": "cleanup", "confirmed": True})
        self.assertEqual(status, 202)
        self.assertTrue(entered.wait(1))
        self.assertEqual(self.request("POST", "/api/action", {"kind": "status"})[0], 409)
        self.assertTrue(self.request("POST", "/api/cancel", {"id": job["id"]})[1]["accepted"])
        release.set()
        self.server.worker.join(2)
        self.assertEqual(self.backend.actions, [])
        self.assertEqual(self.request("POST", "/api/shutdown", {"confirmed": True})[0], 200)
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())
        with self.assertRaises(RuntimeError):
            self.server.submit({"kind": "status"})
