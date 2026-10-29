"""假 Tk 和可控工作线程验证桌面调度，不创建窗口或读取系统。"""

from pathlib import Path
import unittest
import tempfile
import sys
import types
from unittest.mock import patch

from tests.fixtures import create_application

from cpu_memory_cleanup.interaction import desktop
from cpu_memory_cleanup.contracts import ActionRequest, Capabilities, RunReport, Sample
from .test_cli import FakeBackend as ObservationBackend


class FakeBackend(ObservationBackend):
    def open_memory_observation(self):
        return self.open_observation(with_processes=False, top=10)


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
        self.items = {}

    def pack(self, **kwargs):
        pass

    def grid(self, **kwargs):
        self.options["grid"] = kwargs
        self.options["visible"] = True

    def grid_remove(self):
        self.options["visible"] = False

    def place(self, **kwargs):
        self.options["place"] = kwargs

    def columnconfigure(self, *args, **kwargs):
        pass

    def rowconfigure(self, *args, **kwargs):
        pass

    def set(self, *args):
        pass

    def yview(self, *args):
        pass

    def create_oval(self, *args, **kwargs):
        self.items["track"] = {"coords": args, **kwargs}
        return "track"

    def create_arc(self, *args, **kwargs):
        self.items["arc"] = {"coords": args, **kwargs}
        return "arc"

    def itemconfigure(self, item, **kwargs):
        if DeferredThread.in_worker:
            raise AssertionError("工作线程不能更新 Tk")
        self.items[item].update(kwargs)

    def coords(self, *args):
        if DeferredThread.in_worker:
            raise AssertionError("工作线程不能更新 Tk")
        self.options["coords"] = args

    def bind(self, *args):
        pass

    def winfo_width(self):
        return self.options.get("width", 360)

    def winfo_height(self):
        return self.options.get("height", 220)

    def pack_forget(self):
        pass

    def delete(self, start, end):
        self.options["text"] = ""

    def insert(self, position, text):
        self.options["text"] = text

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

    def set(self, value):
        self.value = value


class FakeTk:
    Frame = LabelFrame = Label = Button = Radiobutton = Text = Scrollbar = Canvas = Widget
    StringVar = Variable


class Root:
    def __init__(self):
        self.jobs = {}
        self.destroyed = False

    def title(self, value):
        pass

    def geometry(self, value):
        self.size = value

    def minsize(self, width, height):
        self.minimum = (width, height)

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
        self.cleanup_calls = []
        self.cancelled = 0
        self.closed = False

    def inspect(self, options, *, cancel_event=None):
        if self.closed:
            return RunReport("run", "inspect", completion="cancelled")
        self.calls.append(("inspect", options))
        return RunReport("run", "inspect", completion="success")

    def maintain(self, request, directory, *, cancel_event=None, lightweight=False):
        if self.closed:
            return RunReport("run", "maintain", completion="cancelled")
        self.calls.append(("maintain", request, directory))
        self.lightweight = lightweight
        return RunReport("run", "maintain", completion="success", action={"attempted": True, "call_outcome": "success"})

    def memory_status(self):
        return RunReport("display", "inspect", completion="success", samples=[self.memory_snapshot()])

    def memory_cleanup(self, directory, *, confirmed=False, confirmation_source="", cancel_event=None):
        self.cleanup_calls.append((directory, confirmed, confirmation_source))
        return self.maintain(ActionRequest("working-set", confirmed=confirmed, confirmation_source=confirmation_source),
                             directory, cancel_event=cancel_event, lightweight=True)

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
        self.root, self.app = Root(), FakeApplication()
        self.window = desktop.MemoryWindow(self.root, self.app, FakeTk, output_dir="chosen")

    def finish_worker(self):
        self.window._worker.finish()
        self.window.pump()

    def test_opening_reads_memory_without_cleaning(self):
        self.assertEqual(self.window.button.cget("text"), "一键清理")
        self.assertEqual(self.window.button.cget("bg"), desktop.GREEN)
        self.assertTrue(self.window.button.cget("takefocus"))
        self.window.start()
        self.finish_worker()
        self.assertEqual(self.app.calls, [("inspect", {"count": 1, "interval": 1, "with_processes": False})])
        self.assertFalse(hasattr(self.window, "source_buttons"))
        self.assertFalse(hasattr(self.window, "mechanism"))

    def test_clean_button_dispatches_working_set_without_diagnosis_prerequisite(self):
        self.window.maintain()
        self.window.maintain()
        self.assertEqual(self.window.button.cget("text"), "正在清理…")
        self.assertEqual(self.app.calls, [])
        self.finish_worker()
        self.assertEqual(len(self.app.calls), 1)
        self.assertEqual(self.app.cleanup_calls, [(Path("chosen"), True, "desktop:memory-clean-button")])
        mode, request, directory = self.app.calls[0]
        self.assertEqual(mode, "maintain")
        self.assertEqual(request.mechanism, "working-set")
        self.assertTrue(request.confirmed)
        self.assertEqual(request.confirmation_source, "desktop:memory-clean-button")
        self.assertEqual(directory.name, "chosen")
        self.assertEqual(self.window.button.cget("text"), "一键清理")

    def test_busy_cleanup_is_not_queued(self):
        self.window.start()
        self.window.maintain()
        self.finish_worker()
        self.assertEqual([c[0] for c in self.app.calls], ["inspect"])
        self.assertIsNone(self.window._worker)
        self.assertFalse(hasattr(self.window, "_pending"))

    def test_refresh_is_single_snapshot_and_never_auto_repeats(self):
        self.window.start()
        self.finish_worker()
        self.assertNotIn(self.window.refresh, self.root.jobs.values())
        self.window.refresh()
        worker = self.window._worker
        self.window.refresh()
        self.assertIs(self.window._worker, worker)
        self.finish_worker()
        self.assertEqual([c[0] for c in self.app.calls], ["inspect", "inspect"])

    def test_reading_failure_preserves_old_values_and_time(self):
        self.window.start()
        self.finish_worker()
        timestamp = self.window.context.cget("text")
        with patch.object(self.app, "inspect", side_effect=OSError("read failed")):
            self.window.refresh()
            self.finish_worker()
        self.assertEqual(timestamp, self.window.context.cget("text"))
        self.assertIn("保留旧读数", self.window.status.cget("text"))
        self.assertEqual(self.window.button.cget("state"), "normal")

    def test_memory_percent_and_zero_do_not_use_commit_or_cpu(self):
        report = RunReport("mem", "inspect", samples=[Sample("mem", 0, "2026-10-26T08:00:00+08:00", {
            "physical": {"total_bytes": 8 * 1024 ** 3, "available_bytes": 2 * 1024 ** 3},
            "commit": {"used_bytes": 0, "limit_bytes": 16 * 1024 ** 3}})])
        with patch.object(self.app, "inspect", return_value=report):
            self.window.start()
            self.finish_worker()
        self.assertEqual(self.window.percent.cget("text"), "75%")
        self.assertIn("6.000 GiB", self.window.available.cget("text"))
        self.assertIn("0.000 GiB", self.window.commit.cget("text"))
        self.assertIn("2026-10-26", self.window.context.cget("text"))
        self.assertEqual(self.window.ring.items[self.window._ring_fill]["extent"], -270)

    def test_unknown_and_inconsistent_memory_never_become_zero(self):
        for metrics in ({}, {"physical": {"total_bytes": 8, "available_bytes": 9}},
                        {"physical": {"total_bytes": 0, "available_bytes": 0}}):
            report = RunReport("mem", "inspect", samples=[Sample("mem", 0, None, metrics)])
            view = desktop._present(report)
            self.assertIsNone(view["percent"])
            self.assertEqual(view["headline"], "未知")

    def test_result_separates_action_completion_and_observed_change(self):
        report = RunReport("mem", "maintain", completion="partial", action={
            "attempted": True, "call_outcome": "success", "privilege_restored": None}, samples=[
            Sample("mem", 0, None, {"physical": {"available_bytes": 2 * 1024 ** 3}}),
            Sample("mem", 1, None, {"physical": {"available_bytes": 3 * 1024 ** 3}})])
        view = desktop._present(report)
        self.assertIn("已执行清理", view["details"])
        self.assertIn("2.000 GiB → 3.000 GiB", view["details"])
        self.assertIn("恢复未确认", view["details"])
        self.assertIn("不代表实际提速", view["details"])

    def test_result_summary_uses_available_snapshot_change(self):
        for after, wording in ((3, "可用内存增加"), (1, "可用内存减少"), (2, "可用内存没有变化")):
            with self.subTest(after=after):
                report = RunReport("mem", "maintain", completion="success", action={
                    "attempted": True, "call_outcome": "success", "privilege_restored": True}, samples=[
                    Sample("mem", 0, None, {"physical": {"available_bytes": 2 * 1024 ** 3}}),
                    Sample("mem", 1, None, {"physical": {"available_bytes": after * 1024 ** 3}})])
                view = desktop._present(report)
                self.assertIn(wording, view["result"])
                self.assertNotIn("释放了", view["result"])
                self.assertIn("2.000 GiB →", view["details"])

    def test_compact_window_starts_with_details_collapsed(self):
        self.assertFalse(self.window._details_open)
        self.assertNotIn("grid", self.window.detail_panel.options)
        width, height = (int(value) for value in self.root.size.split("x"))
        self.assertLess(width, 460)
        self.assertLess(height, 660)
        self.assertEqual(self.window.button.cget("text"), "一键清理")
        self.assertEqual(self.window.ring.items[self.window._ring_fill]["extent"], 0)
        self.window.toggle_details()
        self.assertTrue(self.window._details_open)
        self.assertTrue(self.window.detail_panel.options["visible"])
        self.window.toggle_details()
        self.assertFalse(self.window._details_open)
        self.assertFalse(self.window.detail_panel.options["visible"])

    def test_unsupported_working_set_never_falls_back_to_standby(self):
        self.app.capabilities = Capabilities("fake", "available", ("standby",))
        window = desktop.MemoryWindow(Root(), self.app, FakeTk, output_dir="chosen")
        self.assertEqual(window.button.cget("state"), "disabled")
        window.maintain()
        self.assertIsNone(window._worker)
        self.assertEqual(self.app.calls, [])

    def test_worker_only_updates_widgets_on_main_thread(self):
        self.window.start()
        before = self.window.status.cget("text")
        self.window._worker.finish()
        self.assertEqual(before, self.window.status.cget("text"))
        self.window.pump()
        self.assertIn("完成", self.window.status.cget("text"))

    def test_start_failure_restores_controls_without_retry(self):
        with patch.object(DeferredThread, "start", side_effect=RuntimeError("thread failed")):
            self.window.maintain()
        self.assertIsNone(self.window._worker)
        self.assertEqual(self.window.button.cget("state"), "normal")
        self.assertIn("无法启动", self.window.status.cget("text"))
        self.assertNotIn(self.window.maintain, self.root.jobs.values())
        self.window.close()
        self.assertTrue(self.root.destroyed)

    def test_unknown_busy_core_blocks_new_work_and_close(self):
        self.app.busy = True
        self.window.maintain()
        self.window.refresh()
        self.window.close()
        self.assertEqual(self.app.calls, [])
        self.assertFalse(self.root.destroyed)
        self.app.busy = False
        self.window.pump()
        self.assertTrue(self.root.destroyed)

    def test_close_cancels_worker_and_updates_nothing_after_destruction(self):
        self.window.maintain()
        event = self.window._cancel_event
        self.window.close()
        self.assertTrue(event.is_set())
        self.assertFalse(self.root.destroyed)
        self.finish_worker()
        self.assertTrue(self.root.destroyed)
        self.window.pump()
        self.assertEqual(self.app.calls, [])

    def test_readonly_journey_uses_core_without_maintenance(self):
        backend = FakeBackend()
        app = create_application(backend, backend, sleep=lambda seconds: None)
        window = desktop.MemoryWindow(Root(), app, FakeTk, output_dir="unused")
        window.start()
        window._worker.finish()
        window.pump()
        self.assertEqual(backend.read_calls, 1)
        self.assertEqual(backend.action_calls, 0)
        self.assertEqual(window.percent.cget("text"), "75%")

    def test_cancel_before_worker_enters_core_prevents_maintenance(self):
        backend = FakeBackend()
        app = create_application(backend, backend)
        with tempfile.TemporaryDirectory() as directory:
            window = desktop.MemoryWindow(Root(), app, FakeTk, output_dir=directory)
            window.maintain()
            window.cancel()
            window._worker.finish()
            window.pump()
        self.assertEqual(backend.action_calls, 0)
        self.assertIn("已取消", window.status.cget("text"))

    def test_cancel_after_worker_entry_before_core_begin_prevents_maintenance(self):
        backend = FakeBackend()
        app = create_application(backend, backend)
        begin = app._begin
        def cancel_before_begin(*args, **kwargs):
            with patch.object(DeferredThread, "in_worker", False):
                window.cancel()
            return begin(*args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            window = desktop.MemoryWindow(Root(), app, FakeTk, output_dir=directory)
            window.maintain()
            with patch.object(app, "_begin", side_effect=cancel_before_begin):
                window._worker.finish()
            window.pump()
        self.assertEqual(backend.action_calls, 0)
        self.assertIn("已取消", window.status.cget("text"))


    def live_window(self):
        self.app.memory_display_available = True
        self.current_sample = Sample("display", 0, "2026-10-26T08:00:00+08:00", {
            "physical": {"total_bytes": 8 * 1024 ** 3, "available_bytes": 2 * 1024 ** 3},
            "commit": {"used_bytes": 4 * 1024 ** 3, "limit_bytes": 16 * 1024 ** 3}})
        self.app.memory_snapshot = lambda: self.current_sample
        self.clock = [0.0]
        self.window = desktop.MemoryWindow(self.root, self.app, FakeTk, output_dir="chosen", clock=lambda: self.clock[0])
        self.window.start()
        self.finish_memory()
        return self.window

    def finish_memory(self):
        self.window._memory_worker.finish()
        self.window.pump()

    def next_memory(self):
        self.root.jobs.pop(self.window._memory_job)()
        self.finish_memory()

    def advance_animation(self, seconds):
        self.clock[0] = seconds
        self.root.jobs.pop(self.window._animation_job)()

    def test_live_display_updates_during_cleanup_and_never_dispatches_extra_action(self):
        window = self.live_window()
        window.maintain()
        worker = window._worker
        self.current_sample.metrics["physical"]["available_bytes"] = 6 * 1024 ** 3
        self.next_memory()
        self.advance_animation(0.2)
        self.assertTrue(worker.is_alive())
        self.assertEqual(window.percent.cget("text"), "50%")
        self.assertEqual(window.ring.items[window._ring_fill]["extent"], -180)
        self.assertIn("4.000 GiB", window.available.cget("text"))
        self.assertEqual(self.app.calls, [])
        self.assertIn("正在整理", window.status.cget("text"))
        self.advance_animation(0.4)
        self.assertEqual(window.percent.cget("text"), "25%")
        self.finish_worker()
        self.assertEqual([c[0] for c in self.app.calls], ["maintain"])
        self.assertTrue(self.app.lightweight)

    def test_live_display_preserves_last_cleanup_snapshot_result(self):
        window = self.live_window()
        report = RunReport("mem", "maintain", completion="success", action={
            "attempted": True, "call_outcome": "success", "privilege_restored": True}, samples=[
            Sample("mem", 0, None, {"physical": {"available_bytes": 2 * 1024 ** 3}}),
            Sample("mem", 1, None, {"physical": {"available_bytes": 3 * 1024 ** 3}})])
        with patch.object(self.app, "maintain", return_value=report):
            window.maintain()
            self.finish_worker()
        result, details = window.result.cget("text"), window.details.cget("text")
        note = window.result_note.cget("text")
        self.assertIn("可用内存增加", result)
        self.assertIn("2.000 GiB → 3.000 GiB", note)
        self.current_sample.metrics["physical"]["available_bytes"] = 1024 ** 3
        self.next_memory()
        self.advance_animation(0.4)
        self.assertEqual(window.percent.cget("text"), "88%")
        self.assertEqual(window.result.cget("text"), result)
        self.assertEqual(window.result_note.cget("text"), note)
        self.assertEqual(window.details.cget("text"), details)

    def test_smooth_retarget_starts_from_current_display_and_unknown_does_not_animate_to_zero(self):
        window = self.live_window()
        self.current_sample.metrics["physical"]["available_bytes"] = 6 * 1024 ** 3
        self.next_memory()
        self.advance_animation(0.2)
        self.current_sample.metrics["physical"]["available_bytes"] = 2 * 1024 ** 3
        self.next_memory()
        self.assertEqual(window.percent.cget("text"), "50%")
        self.advance_animation(0.6)
        self.assertEqual(window.percent.cget("text"), "75%")
        self.current_sample = Sample("display", 0, None, {})
        self.next_memory()
        self.assertEqual(window.percent.cget("text"), "未知")
        self.assertIsNone(window._animation_job)

    def test_live_reader_failure_preserves_sample_timestamp_and_readings(self):
        window = self.live_window()
        self.app.memory_status = lambda: RunReport(
            "display-failure", "inspect", completion="failed", errors=["controlled display failure"])
        self.next_memory()
        self.assertEqual(window.percent.cget("text"), "75%")
        self.assertIn("2026-10-26T08:00:00", window.context.cget("text"))
        self.assertIn("读取失败", window.context.cget("text"))
        self.assertEqual(self.app.calls, [])

    def test_close_cancels_poll_and_animation_and_waits_for_display_worker(self):
        window = self.live_window()
        self.current_sample.metrics["physical"]["available_bytes"] = 6 * 1024 ** 3
        self.next_memory()
        animation = window._animation_job
        self.root.jobs.pop(window._memory_job)()
        poll = window._memory_job
        window.close()
        self.assertNotIn(animation, self.root.jobs)
        self.assertNotIn(poll, self.root.jobs)
        self.assertFalse(self.root.destroyed)
        self.finish_memory()
        self.assertTrue(self.root.destroyed)
        window.pump()

    def test_live_reader_start_failure_does_not_disable_cleanup_or_start_other_readers(self):
        window = self.live_window()
        with patch.object(DeferredThread, "start", side_effect=RuntimeError("thread unavailable")):
            self.root.jobs.pop(window._memory_job)()
        self.assertIsNone(window._memory_worker)
        self.assertEqual(window.button.cget("state"), "normal")
        self.assertIn("未启动", window.context.cget("text"))
        self.assertEqual(self.app.calls, [])

class DesktopRunTests(unittest.TestCase):
    def test_mainloop_return_joins_live_display_reader_without_cleaning(self):
        class ReturningRoot(Root):
            def mainloop(self):
                self.worker = self.close_callback.__self__._memory_worker
        app, root = FakeApplication(), ReturningRoot()
        app.memory_display_available = True
        app.memory_snapshot = lambda: Sample("display", 0, None, {})
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.run_desktop(root, app, directory), 0)
        self.assertTrue(root.worker.joined)
        self.assertFalse(root.worker.is_alive())
        self.assertTrue(root.destroyed)
        self.assertEqual(app.calls, [])

    def run_desktop(self, root, app, directory):
        class JoiningThread(DeferredThread):
            joined = False

            def join(self):
                self.joined = True
                if self.alive:
                    self.finish()

        tk = types.SimpleNamespace(**{name: getattr(FakeTk, name) for name in
                                   ("Frame", "LabelFrame", "Label", "Button", "Radiobutton", "Text", "Scrollbar", "Canvas", "StringVar")},
                                   Tk=lambda: root, TclError=RuntimeError,
                                   filedialog=types.SimpleNamespace(askdirectory=lambda **kwargs: None),
                                   messagebox=types.SimpleNamespace(askyesno=lambda *args, **kwargs: True))
        with patch.dict(sys.modules, {"tkinter": tk}), patch.object(desktop.threading, "Thread", JoiningThread):
            return desktop.run(app, {"output_dir": directory})

    def test_mainloop_interrupt_cancels_and_joins_undispatched_maintenance(self):
        interruption = KeyboardInterrupt("mainloop interrupted")

        class InterruptedRoot(Root):
            def mainloop(self):
                window = self.close_callback.__self__
                window.refresh()
                window._worker.finish()
                window.pump()
                window.maintain()
                self.worker = window._worker
                self.cancel_event = window._cancel_event
                raise interruption

        backend = FakeBackend()
        app = create_application(backend, backend, sleep=lambda seconds: None)
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
                window.refresh()
                window.maintain()
                self.worker = window._worker
                self.cancel_event = window._cancel_event

        backend = FakeBackend()
        app = create_application(backend, backend)
        root = ReturningRoot()
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.run_desktop(root, app, directory), 0)
        self.assertTrue(app._closed)
        self.assertTrue(root.cancel_event.is_set())
        self.assertFalse(hasattr(root.close_callback.__self__, "_pending"))
        self.assertTrue(root.worker.joined)
        self.assertFalse(root.worker.is_alive())
        self.assertTrue(root.destroyed)
        self.assertEqual((backend.read_calls, backend.action_calls), (0, 0))

    def test_mainloop_error_after_root_destroy_still_joins_and_preserves_error(self):
        failure = ValueError("mainloop failed")

        class DestroyedRoot(Root):
            def mainloop(self):
                self.close_callback.__self__.refresh()
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
        app = create_application(backend, backend)
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
                window.refresh()
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
