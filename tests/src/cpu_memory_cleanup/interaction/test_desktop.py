from tests.support import make_app
"""假 Tk 和可控工作线程验证桌面调度，不创建窗口或读取系统。"""

import unittest
import tempfile
import sys
import types
from unittest.mock import patch

from cpu_memory_cleanup.interaction import desktop
from cpu_memory_cleanup.contracts import Capabilities, RunReport, Sample
from cpu_memory_cleanup.application.core import Application
from .test_cli import FakeBackend


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
    Frame = LabelFrame = Label = Button = Radiobutton = Widget
    StringVar = Variable


class Root:
    def __init__(self):
        self.jobs = {}
        self.destroyed = False

    def title(self, value):
        pass

    def geometry(self, value):
        pass

    def minsize(self, width, height):
        pass

    def configure(self, **kwargs):
        pass

    def protocol(self, name, function):
        self.close_callback = function

    def after(self, milliseconds, function):
        if self.destroyed:
            raise AssertionError("不能向销毁后的窗口投递")
        key = max(self.jobs, default=0) + 1
        self.jobs[key] = function
        return key

    def after_cancel(self, key):
        self.jobs.pop(key, None)

    def destroy(self):
        self.destroyed = True


class FakeApplication:
    capabilities = Capabilities("fake", "available", ("working-set", "standby"))
    busy = False

    def __init__(self):
        self.calls = []
        self.cancelled = 0
        self.closed = False

    def inspect(self, options, *, cancel_event=None):
        if self.closed:
            return RunReport("run", "inspect", completion="cancelled")
        self.calls.append(("inspect", options))
        return RunReport("run", "inspect", completion="success")

    def maintain(self, request, directory, *, cancel_event=None):
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
        self.assertIn("只读诊断：已完成", self.window.status.cget("text"))

    def test_worker_only_queues_result_until_main_thread_pumps(self):
        self.window.start()
        before = self.window.result.cget("text")
        self.window._worker.finish()
        self.assertEqual(self.window.result.cget("text"), before)
        self.window.pump()
        self.assertEqual(self.window.metric_labels["physical"].cget("text"), "未知")
        self.assertIn("尚未取得样本", self.window.result.cget("text"))

    def test_dashboard_renders_zero_and_known_memory_with_sample_time(self):
        sample = Sample("fake", 1, "2026-10-21T08:00:01+08:00", {
            "physical": {"available_bytes": 2 * 1024 ** 3, "total_bytes": 8 * 1024 ** 3},
            "commit": {"used_bytes": 3 * 1024 ** 3, "limit_bytes": 16 * 1024 ** 3}})
        report = RunReport("fake", "inspect", completion="success", samples=[sample],
                           analysis={"cpu": {"intervals": [{"busy_pct": 0, "scope": "system", "coverage": "complete"}]}})
        with patch.object(self.app, "inspect", return_value=report):
            self.window.start()
            self.finish_worker()
        self.assertEqual(self.window.metric_labels["cpu"].cget("text"), "0.0%")
        self.assertEqual(self.window.metric_labels["physical"].cget("text"), "2.000 GiB")
        self.assertEqual(self.window.metric_labels["commit"].cget("text"), "3.000 GiB")
        self.assertIn("全机", self.window.metric_notes["cpu"].cget("text"))
        self.assertIn(sample.timestamp, self.window.context.cget("text"))

    def test_missing_readings_keep_unknown_and_show_reasons(self):
        sample = Sample("fake", 0, "2026-10-21T08:00:00+08:00", {}, errors=["PDH 读取不可用"])
        report = RunReport("fake", "inspect", samples=[sample],
                           analysis={"cpu": {"intervals": [{"busy_pct": None, "coverage": "unknown", "missing_reason": "首拍没有前一累计值"}]}},
                           errors=[{"message": "读取权限不足"}])
        result = desktop._present(report)
        self.assertEqual(result["cards"]["cpu"][0], "未知")
        self.assertEqual(result["cards"]["physical"][0], "未知")
        self.assertIn("首拍没有前一累计值", result["details"])
        self.assertIn("PDH 读取不可用", result["details"])
        self.assertIn("读取权限不足", result["details"])

    def test_group_cpu_is_explicitly_not_system_coverage(self):
        report = RunReport("fake", "inspect", analysis={"cpu": {"intervals": [
            {"busy_pct": 25, "scope": "group", "coverage": "partial"}]}})
        result = desktop._present(report)
        self.assertIn("当前处理器组", result["cards"]["cpu"][1])
        self.assertIn("覆盖不全", result["cards"]["cpu"][1])
        self.assertIn("不能代表全机", result["details"])

    def test_refresh_replaces_poll_and_never_submits_maintenance(self):
        self.window.start()
        self.finish_worker()
        old_poll = self.window._poll_job
        self.window.refresh()
        active_worker = self.window._worker
        self.window.refresh()
        self.assertIs(self.window._worker, active_worker)
        self.assertNotIn(old_poll, self.root.jobs)
        self.finish_worker()
        self.assertEqual([call[0] for call in self.app.calls], ["inspect", "inspect"])

    def test_refresh_does_not_replace_pending_maintenance(self):
        self.window.start()
        self.window.maintain()
        pending = self.window._pending
        worker = self.window._worker
        self.window.refresh()
        self.assertIs(self.window._pending, pending)
        self.assertIs(self.window._worker, worker)
        self.assertEqual(self.app.calls, [])

    def test_readonly_controls_follow_worker_and_close_state(self):
        self.assertEqual(self.window.cancel_button.cget("state"), "disabled")
        self.window.start()
        self.assertEqual(self.window.refresh_button.cget("state"), "disabled")
        self.assertEqual(self.window.cancel_button.cget("state"), "normal")
        self.finish_worker()
        self.assertEqual(self.window.refresh_button.cget("state"), "normal")
        self.assertEqual(self.window.cancel_button.cget("state"), "disabled")
        self.window.close()
        self.window.refresh()
        self.assertEqual(self.window.refresh_button.cget("state"), "disabled")
        self.assertEqual(self.window.cancel_button.cget("state"), "disabled")
        self.assertEqual(len(self.app.calls), 1)

    def test_readonly_journey_uses_core_without_maintenance(self):
        class Backend:
            def __init__(self):
                self.reads = self.closes = self.actions = 0

            def open_observation(self, **options):
                return self

            def read(self, run_id, sequence):
                self.reads += 1
                return Sample(run_id, sequence, f"2026-10-21T00:00:0{sequence}+00:00", {
                    "cpu": {"scope": "system", "coverage": "complete", "logical_processors": 4,
                            "processor_group": 0, "total_seconds": sequence * 4, "idle_seconds": sequence * 2},
                    "physical": {"available_bytes": 2 * 1024 ** 3, "total_bytes": 8 * 1024 ** 3},
                    "commit": {"used_bytes": 3 * 1024 ** 3, "limit_bytes": 16 * 1024 ** 3}})

            def close(self):
                self.closes += 1
                return {"closed": True, "errors": []}

            def open_operation(self, request):
                self.actions += 1
                raise AssertionError("只读旅程不能打开维护会话")

        backend = Backend()
        app = make_app(backend, backend, sleep=lambda seconds: None)
        window = desktop.MemoryWindow(Root(), app, FakeTk, lambda: "unused")
        window.start()
        window._worker.finish()
        window.pump()
        self.assertEqual(window.metric_labels["cpu"].cget("text"), "50.0%")
        self.assertEqual(window.metric_labels["physical"].cget("text"), "2.000 GiB")
        self.assertIn("本次 2 拍", window.context.cget("text"))
        self.assertEqual(backend.reads, 2)
        self.assertEqual(backend.closes, 1)
        self.assertEqual(backend.actions, 0)

    def test_maintenance_waits_for_reader_and_ignores_duplicate_click(self):
        self.window.start()
        self.window.maintain()
        self.window.maintain()
        self.assertEqual(self.app.calls, [])
        self.finish_worker()
        self.assertEqual(len(self.app.calls), 1)
        self.assertIn("单项维护：执行中", self.window.status.cget("text"))
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
        self.assertIn("待执行维护已撤销", self.window.status.cget("text"))
        self.finish_worker()
        self.assertEqual([call[0] for call in self.app.calls], ["inspect"])
        self.assertEqual(self.app.cancelled, 1)

    def test_cancel_before_worker_enters_core_prevents_maintenance(self):
        backend = FakeBackend()
        app = make_app(backend, backend)
        with tempfile.TemporaryDirectory() as directory:
            window = desktop.MemoryWindow(Root(), app, FakeTk, lambda: directory)
            window.maintain()
            self.assertFalse(app.busy)
            window.cancel()
            window._worker.finish()
            window.pump()
        self.assertEqual(backend.action_calls, 0)
        self.assertIn("单项维护：已取消", window.status.cget("text"))
        self.assertIn("维护：未调用", window.result.cget("text"))

    def test_cancel_after_worker_entry_before_core_begin_prevents_maintenance(self):
        backend = FakeBackend()
        app = make_app(backend, backend)
        begin = app._begin

        def cancel_before_begin(*args, **kwargs):
            self.assertTrue(DeferredThread.in_worker)
            self.assertFalse(app.busy)
            with patch.object(DeferredThread, "in_worker", False):
                window.cancel()
            return begin(*args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            window = desktop.MemoryWindow(Root(), app, FakeTk, lambda: directory)
            window.maintain()
            with patch.object(app, "_begin", side_effect=cancel_before_begin):
                window._worker.finish()
            window.pump()
        self.assertEqual(backend.action_calls, 0)
        self.assertIn("单项维护：已取消", window.status.cget("text"))

    def test_initial_thread_start_failure_restores_controls_and_allows_close(self):
        with patch.object(DeferredThread, "start", side_effect=RuntimeError("can't start new thread")):
            self.window.start()
        self.assertIsNone(self.window._worker)
        self.assertIsNone(self.window._cancel_event)
        self.assertEqual(self.window.refresh_button.cget("state"), "normal")
        self.assertEqual(self.window.cancel_button.cget("state"), "disabled")
        self.assertIn("无法启动工作线程", self.window.status.cget("text"))
        self.assertIsNotNone(self.window._poll_job)
        self.window.pump()
        self.window.close()
        self.assertTrue(self.root.destroyed)
        self.assertEqual(self.app.calls, [])

    def test_refresh_thread_start_failure_restores_controls_and_allows_close(self):
        self.window.start()
        self.finish_worker()
        with patch.object(DeferredThread, "start", side_effect=RuntimeError("can't start new thread")):
            self.window.refresh()
        self.assertIsNone(self.window._worker)
        self.assertIsNone(self.window._cancel_event)
        self.assertEqual(self.window.refresh_button.cget("state"), "normal")
        self.assertEqual(self.window.cancel_button.cget("state"), "disabled")
        self.assertIsNotNone(self.window._poll_job)
        self.window.pump()
        self.window.close()
        self.assertTrue(self.root.destroyed)
        self.assertEqual([call[0] for call in self.app.calls], ["inspect"])

    def test_maintenance_thread_start_failure_discards_pending_action(self):
        self.window.start()
        self.finish_worker()
        with patch.object(DeferredThread, "start", side_effect=RuntimeError("can't start new thread")):
            self.window.maintain()
        self.assertIsNone(self.window._worker)
        self.assertIsNone(self.window._cancel_event)
        self.assertIsNone(self.window._pending)
        self.assertEqual(self.window.button.cget("state"), "normal")
        self.assertEqual(self.window.refresh_button.cget("state"), "normal")
        self.assertEqual(self.window.cancel_button.cget("state"), "disabled")
        self.assertIn("无法启动工作线程", self.window.status.cget("text"))
        self.window.pump()
        self.assertEqual([call[0] for call in self.app.calls], ["inspect"])
        self.assertIsNotNone(self.window._poll_job)
        self.assertEqual(list(self.root.jobs.values()).count(self.window.inspect), 1)
        self.root.jobs.pop(self.window._poll_job)()
        self.finish_worker()
        self.assertEqual([call[0] for call in self.app.calls], ["inspect", "inspect"])
        self.window.close()
        self.assertTrue(self.root.destroyed)

    def test_queued_maintenance_start_failure_restores_single_readonly_poll(self):
        self.window.start()
        self.window.maintain()
        with patch.object(DeferredThread, "start", side_effect=RuntimeError("can't start new thread")):
            self.finish_worker()
        self.assertIsNone(self.window._worker)
        self.assertIsNone(self.window._pending)
        self.assertIsNotNone(self.window._poll_job)
        self.assertEqual(list(self.root.jobs.values()).count(self.window.inspect), 1)
        self.root.jobs.pop(self.window._poll_job)()
        self.finish_worker()
        self.assertEqual([call[0] for call in self.app.calls], ["inspect", "inspect"])
        self.window.close()
        self.assertTrue(self.root.destroyed)

    def test_close_waits_for_worker_and_never_dispatches_pending_action(self):
        self.window.start()
        self.window.maintain()
        cancel_event = self.window._cancel_event
        self.window.close()
        self.assertTrue(cancel_event.is_set())
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
        app = make_app(backend, backend)
        root = Root()
        with tempfile.TemporaryDirectory() as directory:
            window = desktop.MemoryWindow(root, app, FakeTk, lambda: directory)
            window.maintain()
            self.assertFalse(app.busy)
            window.close()
            self.assertFalse(root.destroyed)
            with self.assertLogs("cpu_memory_cleanup", level="WARNING"):
                window._worker.finish()
            window.pump()
        self.assertTrue(root.destroyed)
        self.assertFalse(app.busy)
        self.assertEqual(backend.calls, [])


class DesktopRunTests(unittest.TestCase):
    def run_desktop(self, root, app, directory):
        class JoiningThread(DeferredThread):
            joined = False

            def join(self):
                self.joined = True
                if self.alive:
                    self.finish()

        tk = types.SimpleNamespace(**{name: getattr(FakeTk, name) for name in
                                   ("Frame", "LabelFrame", "Label", "Button", "Radiobutton", "StringVar")},
                                   Tk=lambda: root, TclError=RuntimeError,
                                   filedialog=types.SimpleNamespace(askdirectory=lambda **kwargs: None))
        with patch.dict(sys.modules, {"tkinter": tk}), patch.object(desktop.threading, "Thread", JoiningThread):
            return desktop.run(app, {"output_dir": directory})

    def test_mainloop_interrupt_cancels_and_joins_undispatched_maintenance(self):
        interruption = KeyboardInterrupt("mainloop interrupted")

        class InterruptedRoot(Root):
            def mainloop(self):
                window = self.close_callback.__self__
                window.maintain()
                window._worker.finish()
                window.pump()
                self.worker = window._worker
                self.cancel_event = window._cancel_event
                raise interruption

        backend = FakeBackend()
        app = make_app(backend, backend, sleep=lambda seconds: None)
        root = InterruptedRoot()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.run_desktop(root, app, directory)
        self.assertIs(caught.exception, interruption)
        self.assertTrue(app._closed)
        self.assertTrue(root.cancel_event.is_set())
        self.assertTrue(root.worker.joined)
        self.assertFalse(root.worker.is_alive())
        self.assertTrue(root.destroyed)
        self.assertEqual(backend.action_calls, 0)
        self.assertEqual(backend.read_calls, 1)

    def test_mainloop_unexpected_return_discards_pending_maintenance_and_joins_reader(self):
        class ReturningRoot(Root):
            def mainloop(self):
                window = self.close_callback.__self__
                window.maintain()
                self.worker = window._worker
                self.cancel_event = window._cancel_event

        backend = FakeBackend()
        app = make_app(backend, backend)
        root = ReturningRoot()
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.run_desktop(root, app, directory), 0)
        self.assertTrue(app._closed)
        self.assertTrue(root.cancel_event.is_set())
        self.assertIsNone(root.close_callback.__self__._pending)
        self.assertTrue(root.worker.joined)
        self.assertFalse(root.worker.is_alive())
        self.assertTrue(root.destroyed)
        self.assertEqual((backend.read_calls, backend.action_calls), (0, 0))

    def test_mainloop_error_after_root_destroy_still_joins_and_preserves_error(self):
        failure = ValueError("mainloop failed")

        class DestroyedRoot(Root):
            def mainloop(self):
                self.worker = self.close_callback.__self__._worker
                self.destroy()
                raise failure

            def destroy(self):
                if self.destroyed:
                    raise RuntimeError("Tk has already been destroyed")
                super().destroy()

            def after_cancel(self, key):
                if self.destroyed:
                    raise RuntimeError("Tk has already been destroyed")
                super().after_cancel(key)

        backend = FakeBackend()
        app = make_app(backend, backend)
        root = DestroyedRoot()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError) as caught:
                self.run_desktop(root, app, directory)
        self.assertIs(caught.exception, failure)
        self.assertTrue(app._closed)
        self.assertTrue(root.worker.joined)
        self.assertFalse(root.worker.is_alive())
        self.assertEqual((backend.read_calls, backend.action_calls), (0, 0))

    def test_normal_close_returns_without_destroying_root_twice(self):
        class ClosingRoot(Root):
            def mainloop(self):
                window = self.close_callback.__self__
                self.worker = window._worker
                window.close()
                self.worker.finish()
                window.pump()

            def destroy(self):
                if self.destroyed:
                    raise AssertionError("不能重复销毁已关闭窗口")
                super().destroy()

        app = FakeApplication()
        root = ClosingRoot()
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.run_desktop(root, app, directory), 0)
        self.assertTrue(root.destroyed)
        self.assertTrue(app.closed)
        self.assertFalse(root.worker.is_alive())
        self.assertEqual(app.calls, [])
