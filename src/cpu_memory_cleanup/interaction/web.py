"""显式注入Package的回环HTTP适配。导入、启动和页面GET均不读系统。"""

from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import secrets
import threading
from urllib.parse import parse_qs, urlsplit

from ..application.targets import target_key, targets_from_rows
from ..contracts import json_pairs

MAX_BODY = 1024 * 1024


class LocalWebServer(ThreadingHTTPServer):
    """同一时间一个业务工作线程；HTTP查询不会重放动作。"""
    daemon_threads = False
    allow_reuse_address = False

    def __init__(self, app, output_dir, *, port=0, preview=False):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("端口必须为0..65535")
        if type(preview) is not bool:
            raise ValueError("preview必须是布尔值")
        self.app, self.output_dir, self.preview = app, output_dir, preview
        self.token = secrets.token_urlsafe(32)
        self.jobs, self.targets = OrderedDict(), {}
        self.lock = threading.Lock()
        self.worker = None
        self.cancel_event = None
        self.stopping = False
        super().__init__(("127.0.0.1", port), Handler)
        self.authorities = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_port}/"

    def submit(self, value):
        if not isinstance(value, dict):
            raise ValueError("请求必须是对象")
        kind = value.get("kind")
        allowed = {"status": {"kind"}, "targets": {"kind"},
                   "cleanup": {"kind", "confirmed"}, "batch": {"kind", "targets", "confirmed"},
                   "replay": {"kind", "source_job"}}
        if kind not in allowed or set(value) - allowed[kind]:
            raise ValueError("未知动作或请求字段")
        if kind in ("cleanup", "batch") and value.get("confirmed") is not True:
            raise ValueError("整理请求需要本次明确确认")
        with self.lock:
            if self.stopping or (self.worker is not None and self.worker.is_alive()):
                raise RuntimeError("当前有任务正在收尾，请稍后重试")
            if kind == "batch":
                keys = value.get("targets")
                if (not isinstance(keys, list) or not 1 <= len(keys) <= 4096
                        or any(not isinstance(key, str) for key in keys) or len(keys) != len(set(keys))):
                    raise ValueError("需要明确选择不重复的目标")
                if any(key not in self.targets for key in keys):
                    raise ValueError("选择已失效，请重新读取列表")
                targets = tuple(self.targets[key] for key in keys)
            if kind == "replay":
                source = self.jobs.get(value.get("source_job"))
                if not source or source["state"] != "finished" or not source.get("report", {}).get("output_dir"):
                    raise ValueError("只能回看本会话已有运行，不接受文件路径")
                source_path = source["report"]["output_dir"]
            job_id = secrets.token_hex(12)
            self.jobs[job_id] = {"id": job_id, "kind": kind, "state": "running"}
            while len(self.jobs) > 32:
                self.jobs.popitem(last=False)
            event = threading.Event()
            self.cancel_event = event

            def work():
                try:
                    if kind == "status":
                        report = self.app.memory_status()
                    elif kind == "targets":
                        report = self.app.memory_targets(cancel_event=event)
                    elif kind == "cleanup":
                        report = self.app.memory_cleanup(self.output_dir, confirmed=True,
                            confirmation_source="localhost:explicit-global-button", cancel_event=event)
                    elif kind == "batch":
                        report = self.app.memory_batch_cleanup(targets, self.output_dir, confirmed=True,
                            confirmation_source="localhost:explicit-selected-button", cancel_event=event)
                    else:
                        report = self.app.memory_result(source_path)
                    result = report.to_dict()
                    with self.lock:
                        if kind == "targets":
                            rows = report.samples[0].processes if report.samples else []
                            self.targets = {f"{t.pid}:{t.created_at}": t for t in targets_from_rows(rows)}
                        self.jobs[job_id].update(state="finished", report=result)
                except Exception as error:
                    with self.lock:
                        self.jobs[job_id].update(state="error", error=str(error))
            self.worker = threading.Thread(target=work, name="local-web-job", daemon=False)
            try:
                self.worker.start()
            except Exception:
                self.jobs.pop(job_id, None)
                self.worker = None
                raise
        return {"id": job_id, "state": "running"}

    def job(self, identity):
        with self.lock:
            if identity not in self.jobs:
                raise ValueError("本会话没有该任务")
            return dict(self.jobs[identity])

    def cancel(self, identity):
        with self.lock:
            job = self.jobs.get(identity)
            if not job or job["state"] != "running":
                return {"accepted": False}
            if self.cancel_event is not None:
                self.cancel_event.set()
        self.app.request_cancel()
        return {"accepted": True, "note": "只阻止尚未派发的动作，已发生事实仍保留"}

    def stop(self):
        with self.lock:
            self.stopping = True
            worker = self.worker
            if self.cancel_event is not None:
                self.cancel_event.set()
        self.app.shutdown()
        self.shutdown()
        if worker is not None:
            worker.join()


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalMemory/1"

    def log_message(self, format, *args):
        # 不记录会话令牌、进程路径或请求正文。
        pass

    def respond(self, code, value, *, content_type="application/json; charset=utf-8"):
        content = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(content)

    def valid_request(self, *, session=False):
        authority = self.headers.get("Host")
        if authority not in self.server.authorities:
            self.respond(403, {"error": "仅接受当前本地地址"})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{authority}":
            self.respond(403, {"error": "请求来源不匹配"})
            return False
        if session and not secrets.compare_digest(self.headers.get("X-Session-Token", ""), self.server.token):
            self.respond(403, {"error": "会话无效，请重新打开本地页面"})
            return False
        return True

    def do_GET(self):
        if not self.valid_request():
            return
        parsed = urlsplit(self.path)
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/styles.css": ("styles.css", "text/css; charset=utf-8")}
        if parsed.path in assets:
            name, mime = assets[parsed.path]
            self.respond(200, files("cpu_memory_cleanup.interaction").joinpath("web_assets", name).read_bytes(), content_type=mime)
        elif parsed.path == "/api/session":
            self.respond(200, {"token": self.server.token, "preview": self.server.preview,
                "capabilities": {"mechanisms": self.server.app.capabilities.mechanisms,
                                 "scopes": self.server.app.capabilities.scopes}})
        elif parsed.path == "/api/job":
            if not self.valid_request(session=True):
                return
            try:
                query = parse_qs(parsed.query, strict_parsing=True)
                if set(query) != {"id"} or len(query["id"]) != 1:
                    raise ValueError("需要任务标识")
                self.respond(200, self.server.job(query["id"][0]))
            except (ValueError, KeyError) as error:
                self.respond(400, {"error": str(error)})
        else:
            self.respond(404, {"error": "没有该入口"})

    def do_POST(self):
        if not self.valid_request(session=True):
            return
        try:
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("请求需要JSON")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise ValueError("请求大小无效")
            self.connection.settimeout(5)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("请求不完整")
            value = json.loads(raw, object_pairs_hook=json_pairs)
            if not isinstance(value, dict):
                raise ValueError("请求需要对象")
            if self.path == "/api/action":
                self.respond(202, self.server.submit(value))
            elif self.path == "/api/cancel" and set(value) == {"id"} and isinstance(value["id"], str):
                self.respond(200, self.server.cancel(value["id"]))
            elif self.path == "/api/shutdown" and value == {"confirmed": True}:
                self.respond(200, {"stopping": True})
                threading.Thread(target=self.server.stop, name="local-web-stop", daemon=False).start()
            else:
                self.respond(404, {"error": "没有该入口"})
        except RuntimeError as error:
            self.respond(409, {"error": str(error)})
        except (ValueError, TypeError, OSError) as error:
            self.respond(400, {"error": str(error)})
