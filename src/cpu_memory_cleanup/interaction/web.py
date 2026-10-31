"""显式注入Package的回环HTTP适配。导入、启动和页面GET均不读系统。"""

from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import secrets
import threading
import time
from dataclasses import asdict
from urllib.parse import parse_qs, urlsplit

from ..application.targets import target_key, targets_from_rows
from ..application.targets import plan_targets, validate_rules
from ..application.settings import validate_preferences
from ..application.policy import Policy, Rule
from .presentation import summary
from ..contracts import ActionRequest
from ..contracts import json_pairs

MAX_BODY = 1024 * 1024


class LocalWebServer(ThreadingHTTPServer):
    """同一时间一个业务工作线程；HTTP查询不会重放动作。"""
    daemon_threads = False
    allow_reuse_address = False

    def __init__(self, app, output_dir, *, port=0, preview=False, preferences=None, clock=None):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("端口必须为0..65535")
        if type(preview) is not bool:
            raise ValueError("preview必须是布尔值")
        self.app, self.output_dir, self.preview = app, output_dir, preview
        self.token = secrets.token_urlsafe(32)
        self.jobs, self.targets = OrderedDict(), {}
        self.lock = threading.Lock()
        self.display_lock = threading.Lock()
        self.worker = None
        self.cancel_event = None
        self.stopping = False
        self.preferences = preferences
        self.settings = preferences.load() if preferences is not None else validate_preferences({"schema_version": 1})
        self.policy = Policy(Rule(**self.settings.get("rule", {})))
        self.clock = clock or time.monotonic
        self.rule_targets = ()
        self.rule_protections = []
        self.rule_job = None
        self.rule_result_job = None
        self.rule_error = ""
        self.rule_poll_at = 0
        self.rule_seen = None
        super().__init__(("127.0.0.1", port), Handler)
        self.authorities = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_port}/"

    def submit(self, value, *, automatic=False, rule_policy=None):
        if not isinstance(value, dict):
            raise ValueError("请求必须是对象")
        kind = value.get("kind")
        allowed = {"status": {"kind"}, "targets": {"kind"},
                   "cleanup": {"kind", "confirmed", "mechanism"}, "batch": {"kind", "targets", "confirmed"},
                   "replay": {"kind", "source_job"}}
        if kind not in allowed or set(value) - allowed[kind]:
            raise ValueError("未知动作或请求字段")
        if kind in ("cleanup", "batch") and value.get("confirmed") is not True:
            raise ValueError("整理请求需要本次明确确认")
        mechanism = value.get("mechanism", "working-set")
        if kind == "cleanup" and mechanism not in self.app.capabilities.mechanisms:
            raise ValueError("当前系统不支持该整理方式")
        with self.lock:
            if self.stopping or (self.worker is not None and self.worker.is_alive()):
                raise RuntimeError("当前有任务正在收尾，请稍后重试")
            if automatic and (not self.policy.armed or rule_policy is not self.policy):
                raise RuntimeError("规则已停用或替换，拒绝旧触发")
            if kind == "batch":
                keys = value.get("targets")
                if (not isinstance(keys, list) or not 1 <= len(keys) <= 4096
                        or any(not isinstance(key, str) for key in keys) or len(keys) != len(set(keys))):
                    raise ValueError("需要明确选择不重复的目标")
                if any(key not in self.targets for key in keys):
                    raise ValueError("选择已失效，请重新读取列表")
                targets = self.rule_targets if automatic else tuple(self.targets[key] for key in keys)
                protections = [dict(rule) for rule in (self.rule_protections if automatic else self.settings["protections"])]
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
                        if mechanism == "working-set":
                            report = self.app.memory_cleanup(self.output_dir, confirmed=True,
                                confirmation_source="localhost:explicit-global-button", cancel_event=event)
                        else:
                            report = self.app.maintain(ActionRequest(mechanism, confirmed=True,
                                confirmation_source="localhost:explicit-advanced-button"),
                                self.output_dir, cancel_event=event, lightweight=True)
                    elif kind == "batch":
                        report = self.app.memory_batch_cleanup(targets, self.output_dir, confirmed=True,
                            protections=protections,
                            confirmation_source="localhost:armed-rule" if automatic else "localhost:explicit-selected-button", cancel_event=event)
                    else:
                        report = self.app.memory_result(source_path)
                    result = report.to_dict()
                    with self.lock:
                        if kind == "targets":
                            rows = report.samples[0].processes if report.samples else []
                            self.targets = {f"{t.pid}:{t.created_at}": t for t in targets_from_rows(rows)}
                        self.jobs[job_id].update(state="finished", report=result, presentation=summary(report))
                        if automatic and report.completion != "success":
                            self.policy.stop()
                            self.rule_error = "规则动作未完整成功，已停用；不会自动重试"
                except Exception as error:
                    with self.lock:
                        self.jobs[job_id].update(state="error", error=str(error))
                        if automatic:
                            self.policy.stop()
                            self.rule_error = "规则调用异常，已停用"
            self.worker = threading.Thread(target=work, name="local-web-job", daemon=False)
            try:
                self.worker.start()
                if kind in ("cleanup", "batch") and self.policy.armed:
                    self.policy.dispatched(self.clock())
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
            self.policy.stop()
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
            self.policy.stop()
            worker = self.worker
            if self.cancel_event is not None:
                self.cancel_event.set()
        self.app.shutdown()
        self.shutdown()
        if worker is not None:
            worker.join()

    def state(self):
        with self.lock:
            return {"settings": dict(self.settings), "rule": {"armed": self.policy.armed,
                    "target_count": len(self.rule_targets), "last_job": self.rule_result_job or self.rule_job,
                    "error": self.rule_error}, "busy": self.worker is not None and self.worker.is_alive()}

    def display(self):
        """轻量显示与维护共享Package，显示不占业务任务、不改写动作结果。"""
        with self.display_lock:
            with self.lock:
                if self.stopping:
                    raise RuntimeError("本地服务正在停止")
            return self.app.memory_status().to_dict()

    def save_settings(self, value):
        with self.lock:
            if self.stopping or (self.worker is not None and self.worker.is_alive()):
                raise RuntimeError("任务正在执行，稍后修改设置")
            validated = validate_preferences(value)
            if self.preferences is not None:
                self.preferences.save(validated)
            self.policy.stop()
            self.settings = validated
        return self.state()

    def configure_rule(self, value):
        if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
            raise ValueError("需要明确规则开关")
        if not value["enabled"]:
            with self.lock:
                self.policy.stop()
            return self.state()
        if set(value) != {"enabled", "confirmed", "targets", "rule"} or value.get("confirmed") is not True:
            raise ValueError("启用规则需要当前范围和明确确认")
        with self.lock:
            if self.stopping or (self.worker is not None and self.worker.is_alive()):
                raise RuntimeError("当前忙碌，不能启用规则")
            keys = value["targets"]
            if not isinstance(keys, list) or any(not isinstance(key, str) or key not in self.targets for key in keys):
                raise ValueError("规则目标快照失效")
            targets = tuple(self.targets[key] for key in keys)
            plan_targets(targets, self.settings["protections"])
            policy = Policy(Rule(**value["rule"]))
            policy.arm(self.clock(), confirmed=True, source="localhost:explicit-rule-enable")
            settings = {**self.settings, "rule": asdict(policy.rule)}
            if self.preferences is not None:
                self.preferences.save(settings)
            self.settings, self.policy = settings, policy
            self.rule_targets, self.rule_protections = targets, [dict(r) for r in self.settings["protections"]]
            self.rule_error, self.rule_job, self.rule_seen = "", None, None
            self.rule_result_job = None
        return self.state()

    def service_actions(self):
        """服务循环仅在明确武装后调度；未武装时没有系统读取。"""
        now = self.clock()
        with self.lock:
            if self.stopping or not self.policy.armed:
                return
            busy = self.worker is not None and self.worker.is_alive()
            if busy:
                active = next(reversed(self.jobs.values()), None)
                if not active or active["kind"] != "status":
                    self.policy.due(now, busy=True)
                return
            job = self.jobs.get(self.rule_job) if self.rule_job else None
            percent = None
            if self.policy.rule.mode == "pressure":
                if job and job["kind"] == "status" and job["state"] == "finished" and self.rule_seen != job["id"]:
                    self.rule_seen = job["id"]
                    report = job["report"]
                    if report["completion"] == "success" and report["samples"]:
                        physical = report["samples"][-1]["metrics"].get("physical") or {}
                        total, available = physical.get("total_bytes"), physical.get("available_bytes")
                        if total and available is not None and 0 <= available <= total:
                            percent = available / total * 100
                    due = self.policy.due(now, available_pct=percent)
                else:
                    due = False
            else:
                due = self.policy.due(now)
            read_due = self.policy.rule.mode == "pressure" and now - self.rule_poll_at >= 1
            keys = [f"{t.pid}:{t.created_at}" for t in self.rule_targets]
            rule_policy = self.policy
        try:
            if due:
                job = self.submit({"kind": "batch", "targets": keys, "confirmed": True}, automatic=True, rule_policy=rule_policy)
                with self.lock:
                    self.rule_job = job["id"]
                    self.rule_result_job = job["id"]
            elif read_due:
                job = self.submit({"kind": "status"}, automatic=True, rule_policy=rule_policy)
                with self.lock:
                    self.rule_poll_at, self.rule_job = now, job["id"]
        except RuntimeError:
            pass  # 手动请求刚占用唯一工作单元；下次循环重查，不重复动作。
        except (ValueError, TypeError) as error:
            with self.lock:
                self.policy.stop()
                self.rule_error = str(error)


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
                "operating_system": self.server.app.capabilities.operating_system,
                "reading": self.server.app.capabilities.reading,
                "permission": "尚未执行请求前置检查，不根据平台标签认定权限可用",
                "capabilities": {"mechanisms": self.server.app.capabilities.mechanisms,
                                 "scopes": self.server.app.capabilities.scopes}})
        elif parsed.path == "/api/state":
            if self.valid_request(session=True):
                self.respond(200, self.server.state())
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
            elif self.path == "/api/display" and value == {}:
                self.respond(200, self.server.display())
            elif self.path == "/api/settings":
                self.respond(200, self.server.save_settings(value))
            elif self.path == "/api/rule":
                self.respond(200, self.server.configure_rule(value))
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


def serve(server):
    """正式与测试装配共用生命周期；不自动打开页面或派发读取。"""
    print(server.url, flush=True)
    print("假数据预览" if server.preview else "真实系统模式：页面可见时实时读取，工作集按钮点击即整理", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        with server.lock:
            server.stopping = True
            server.policy.stop()
            if server.cancel_event is not None:
                server.cancel_event.set()
        server.app.shutdown()
        if server.worker is not None:
            server.worker.join()
        server.server_close()
    return 0
