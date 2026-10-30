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
        for path in ("/", "/app.js", "/styles.css", "/api/session"):
            status, content = self.request("GET", path, token=False)
            self.assertEqual(status, 200)
        self.assertEqual(self.reads, [])
        self.assertEqual(self.backend.actions, [])
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

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
        self.request("GET", "/api/job?id=" + job["id"])
        self.assertEqual(self.backend.actions, [{"scope": "system", "pid": None}])

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
