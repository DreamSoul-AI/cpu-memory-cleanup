"""localhost假数据网页入口；不启动浏览器、不接真实系统。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def main():
    import argparse
    import ctypes
    import json
    from unittest.mock import patch
    from cpu_memory_cleanup.api import create_application, Capabilities
    from cpu_memory_cleanup.interaction.web import LocalWebServer
    from tests.fixtures import ScenarioBackend, FakeRuntime
    parser = argparse.ArgumentParser(description="仅假数据localhost界面，不采样、不清理主机")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".test-results" / "localhost-preview")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    backend = ScenarioBackend()
    with patch.object(ctypes, "WinDLL", side_effect=AssertionError("假数据禁止真实DLL"), create=True), \
         patch("cpu_memory_cleanup.api.select_backend", side_effect=AssertionError("假数据禁止真实平台")):
        app = create_application(reader=backend, maintainer=backend, runtime=FakeRuntime(),
            capabilities=Capabilities("synthetic", "available", ("working-set",), scopes=("system", "process")))
        server = LocalWebServer(app, args.output_dir / "runs", port=args.port, preview=True)
        print(server.url, flush=True)
        (args.output_dir / "server.json").write_text(json.dumps({"url": server.url, "preview": True,
            "real_sampling": False, "real_cleanup": False}), encoding="utf-8")
        try:
            server.serve_forever(poll_interval=0.1)
        finally:
            app.shutdown()
            if server.worker is not None:
                server.worker.join()
            server.server_close()
            (args.output_dir / "session.json").write_text(json.dumps({"actions": backend.actions,
                "preview": True, "real_dll_allowed": False, "real_sampling": False, "real_cleanup": False},
                ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
