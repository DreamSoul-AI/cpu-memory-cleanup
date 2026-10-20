"""假 Tk 和可控工作线程验证桌面调度，不创建窗口或读取系统。"""

import unittest
import tempfile
from unittest.mock import patch

from cpu_memory_cleanup import desktop
from cpu_memory_cleanup.contracts import RunReport
from cpu_memory_cleanup.core import Application


class DeferredThread:
    in_worker = False

    def __init__(self, target, daemon):
        self.target = target
        self.alive = False
        if daemon:
            raise AssertionError("维护线程必须能够完成收尾")

    def start(self):
        self.alive = True

    def finish(self):
        DeferredThread.in_worker = True
        try:
            self.target()
        finally:
            DeferredThread.in_worker = False
            self.alive = False

    def is_alive(self):
        return self.alive

    def join(self):
        if self.alive:
            raise AssertionError("不能在 UI 线程阻塞等待运行中的工作")


class Widget:
    def __init__(self, *args, **kwargs):
        self.options = {"state": "normal", **kwargs}

    def pack(self, **kwargs):
        pass

    def configure(self, **kwargs):
        if DeferredThread.in_worker:
            raise AssertionError("工作线程不能更新 Tk")
        self.options.update(kwargs)

    def cget(self, key):
        return self.options[key]


class Variable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeTk:
    Label = Button = Radiobutton = Widget
    StringVar = Variable


class Root:
    def __init__(self):
        self.jobs = {}
        self.destroyed = False

    def title(self, value):
        pass

    def geometry(self, value):
        pass

    def protocol(self, name, function):
        self.close_callback = function

    def after(self, milliseconds, function):
        if self.destroyed:
            raise AssertionError("不能向销毁后的窗口投递")
        key = len(self.jobs) + 1
        self.jobs[key] = function
        return key

    def after_cancel(self, key):
        self.jobs.pop(key, None)

    def destroy(self):
        self.destroyed = True


class FakeApplication:
    busy = False

    def __init__(self):
        self.calls = []
        self.cancelled = 0
        self.closed = False

    def inspect(self, options):
        if self.closed:
            return RunReport("run", "inspect", completion="cancelled")
        self.calls.append(("inspect", options))
        return RunReport("run", "inspect", completion="success")

    def maintain(self, request, directory):
        if self.closed:
            return RunReport("run", "maintain", completion="cancelled")
        self.calls.append(("maintain", request, directory))
        return RunReport("run", "maintain", completion="success", action={"attempted": True, "call_outcome": "success"})

    def request_cancel(self):
        self.cancelled += 1
        return True

    def shutdown(self):
        self.closed = True
        self.request_cancel()


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.thread_patch = patch.object(desktop.threading, "Thread", DeferredThread)
        self.thread_patch.start()
        self.addCleanup(self.thread_patch.stop)
        self.root = Root()
        self.app = FakeApplication()
        self.window = desktop.MemoryWindow(self.root, self.app, FakeTk, lambda: "chosen")

    def finish_worker(self):
        self.window._worker.finish()
        self.window.pump()

    def test_opening_only_submits_readonly_inspection(self):
        self.window.start()
        self.finish_worker()
        self.assertEqual(self.app.calls, [("inspect", {"count": 2, "interval": 1})])
        self.assertIn("inspect：success", self.window.status.cget("text"))

    def test_worker_only_queues_result_until_main_thread_pumps(self):
        self.window.start()
        before = self.window.result.cget("text")
        self.window._worker.finish()
        self.assertEqual(self.window.result.cget("text"), before)
        self.window.pump()
        self.assertIn("可用：未知", self.window.result.cget("text"))

    def test_maintenance_waits_for_reader_and_ignores_duplicate_click(self):
        self.window.start()
        self.window.maintain()
        self.window.maintain()
        self.assertEqual(self.app.calls, [])
        self.finish_worker()
        self.assertEqual(len(self.app.calls), 1)
        self.window.maintain()
        self.finish_worker()
        actions = [call for call in self.app.calls if call[0] == "maintain"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0][1].mechanism, "working-set")
        self.assertTrue(actions[0][1].confirmed)
        self.assertEqual(actions[0][2].name, "chosen")

    def test_cancel_drops_undispatched_ui_request(self):
        self.window.start()
        self.window.maintain()
        self.window.cancel()
        self.finish_worker()
        self.assertEqual([call[0] for call in self.app.calls], ["inspect"])
        self.assertEqual(self.app.cancelled, 1)

    def test_close_waits_for_worker_and_never_dispatches_pending_action(self):
        self.window.start()
        self.window.maintain()
        self.window.close()
        self.assertFalse(self.root.destroyed)
        self.assertEqual(self.app.cancelled, 1)
        self.finish_worker()
        self.assertTrue(self.root.destroyed)
        self.assertEqual([call[0] for call in self.app.calls], [])
        self.window.pump()

    def test_unknown_busy_core_blocks_new_action_and_window_destroy(self):
        self.app.busy = True
        self.window.maintain()
        self.assertEqual(self.app.calls, [])
        self.window.close()
        self.assertFalse(self.root.destroyed)
        self.app.busy = False
        self.window.pump()
        self.assertTrue(self.root.destroyed)

    def test_directory_selection_cancel_has_zero_action(self):
        self.window._choose_directory = lambda: ""
        self.window.maintain()
        self.assertEqual(self.app.calls, [])
        self.assertIsNone(self.window._worker)

    def test_close_before_worker_enters_core_rejects_maintenance_atomically(self):
        class ForbiddenBackend:
            def __init__(self):
                self.calls = []

            def open_observation(self, **options):
                self.calls.append("read")
                raise AssertionError("关闭后不能取得读取资源")

            def open_operation(self, request):
                self.calls.append("maintain")
                raise AssertionError("关闭后不能取得维护资源")

        backend = ForbiddenBackend()
        app = Application(backend, backend)
        root = Root()
        with tempfile.TemporaryDirectory() as directory:
            window = desktop.MemoryWindow(root, app, FakeTk, lambda: directory)
            window.maintain()
            self.assertFalse(app.busy)
            window.close()
            self.assertFalse(root.destroyed)
            with self.assertLogs("cpu_memory_cleanup.core", level="WARNING"):
                window._worker.finish()
            window.pump()
        self.assertTrue(root.destroyed)
        self.assertFalse(app.busy)
        self.assertEqual(backend.calls, [])
