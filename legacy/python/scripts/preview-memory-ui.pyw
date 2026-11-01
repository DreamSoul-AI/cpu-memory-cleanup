"""显式假数据体验入口，不用于产品普通启动或真实维护。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def main():
    import argparse
    import ctypes
    import json
    import tkinter as tk
    from unittest.mock import patch
    from cpu_memory_cleanup.api import create_application, Capabilities
    from cpu_memory_cleanup.interaction.desktop import MemoryWindow
    from tests.fixtures import ScenarioBackend, FakeRuntime
    parser = argparse.ArgumentParser(description="仅合成数据窗口，不读取或整理主机")
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".test-results" / "experience")
    options = parser.parse_args()
    options.output_dir.mkdir(parents=True, exist_ok=True)
    backend = ScenarioBackend()
    class FakeTray:
        def __init__(self, callback):
            self.callback = callback
        def start(self):
            pass
        def close(self):
            pass
        def notify(self, text):
            pass
    with patch.object(ctypes, "WinDLL", side_effect=AssertionError("假数据入口禁止真实DLL"), create=True), \
         patch("cpu_memory_cleanup.api.select_backend", side_effect=AssertionError("禁止选择真实平台")):
        app = create_application(reader=backend, maintainer=backend, runtime=FakeRuntime(),
            capabilities=Capabilities("synthetic", "available", ("working-set", "standby"), scopes=("system", "process")))
        root = tk.Tk()
        # Tk缩放只作用于本进程，不改变系统显示设置。
        root.tk.call("tk", "scaling", float(root.tk.call("tk", "scaling")) * options.scale)
        window = MemoryWindow(root, app, tk, output_dir=options.output_dir / "runs",
            settings_path=options.output_dir / "preferences.json", tray_factory=FakeTray)
        root.title("内存清理 · 假数据预览")
        try:
            window.start()
            root.mainloop()
        finally:
            app.shutdown()
            for worker in (window._worker, window._memory_worker):
                if worker is not None:
                    worker.join()
            (options.output_dir / "session.json").write_text(json.dumps({
                "scale": options.scale, "actions": backend.actions,
                "backend": "synthetic", "real_dll_allowed": False,
                "real_sampling": False, "real_cleanup": False}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
