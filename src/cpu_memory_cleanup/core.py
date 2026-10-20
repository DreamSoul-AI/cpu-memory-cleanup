"""用例、运行状态与 JSONL 记录。系统接入由入口装配提供。"""

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import sys
import threading
import uuid

from .contracts import ActionRequest, RULE_VERSION, SCHEMA_VERSION, RunReport, Sample, finite_number, validate_action
from . import rules

LOGGER = logging.getLogger(__name__)
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024
MAX_SAMPLES = 1000
PROGRAM_VERSION = "0.2.0"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _error(report, phase, error):
    item = {"phase": phase, "type": type(error).__name__,
            "message": str(error) if isinstance(error, ValueError) else "操作失败，原始系统错误未写入诊断日志"}
    report.errors.append(item)
    try:
        LOGGER.warning(_json({"timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                       "level": "WARN", "event": "operation_failed", "logger": LOGGER.name,
                       "message": "流程失败，已知事实保留于业务报告", "app_name": "CPU 内存清理",
                       "app_version": PROGRAM_VERSION, "runtime_id": report.run_id,
                       "process_name": Path(sys.executable).name, "pid": os.getpid(),
                       "phase": phase, "exception_type": type(error).__name__}))
    except Exception:
        pass  # 诊断 sink 失败不能改写已发生的业务事实。


def _options(options):
    result = {"count": 1, "interval": 1, "with_processes": False, "top": 10, "conditions": {}}
    if options is not None:
        if not isinstance(options, dict) or set(options) - set(result) - {"output_dir", "jsonl_path"}:
            raise ValueError("采样参数字段无效")
        result.update(options)
    if type(result["count"]) is not int or not 1 <= result["count"] <= MAX_SAMPLES:
        raise ValueError(f"count: 必须是 1..{MAX_SAMPLES} 的整数")
    finite_number(result["interval"], "interval")
    if result["interval"] > 3600:
        raise ValueError("interval: 最大 3600 秒")
    if type(result["with_processes"]) is not bool or type(result["top"]) is not int or not 1 <= result["top"] <= 100:
        raise ValueError("进程采样参数无效")
    if not isinstance(result["conditions"], dict):
        raise ValueError("conditions: 必须是对象")
    _json(result["conditions"])
    result["conditions"] = {"top": result["top"], **result["conditions"]}
    if result.get("output_dir") is not None and result.get("jsonl_path") is not None:
        raise ValueError("output_dir 与 jsonl_path 不能同时指定")
    return result


class _Journal:
    def __init__(self, report, options):
        self.run_id = report.run_id
        self.streams = {}
        self.directory = None
        try:
            if options.get("jsonl_path") is not None:
                path = Path(options["jsonl_path"])
                self.directory = path.parent
            else:
                base = Path(options["output_dir"])
                base.mkdir(parents=True, exist_ok=True)
                self.directory = base / report.run_id
                self.directory.mkdir()
                path = self.directory / "samples.jsonl"
            self.streams["samples"] = path.open("x", encoding="utf-8", newline="\n")
            self.sample_path = path
            report.output_dir = str(self.directory)
        except Exception:
            self.close()
            raise

    def append(self, kind, value):
        if kind not in self.streams:
            self.streams[kind] = (self.directory / "operation.jsonl").open("x", encoding="utf-8", newline="\n")
        stream = self.streams[kind]
        record = {"run_id": self.run_id, **value}
        encoded = _json(record) + "\n"
        if len(encoded.encode("utf-8")) > MAX_LINE_BYTES or stream.tell() + len(encoded.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError("运行记录超出大小上限")
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())

    def close(self):
        errors = []
        for kind, stream in list(self.streams.items()):
            try:
                stream.close()
            except Exception as error:
                errors.append(error)
            else:
                del self.streams[kind]
        return errors

    def commit_end(self, terminal):
        # 临时文件先关闭，再原子替换，避免关闭失败后磁盘留下“整体成功”。
        content = self.sample_path.read_bytes()
        final = (_json({"run_id": self.run_id, **terminal}) + "\n").encode("utf-8")
        if len(content) + len(final) > MAX_FILE_BYTES:
            raise ValueError("结束记录超出大小上限")
        temporary = self.sample_path.with_name(f".{self.run_id}.pending")
        stream = temporary.open("xb")
        self.streams["terminal"] = stream
        stream.write(content + final)
        stream.flush()
        os.fsync(stream.fileno())
        stream.close()
        del self.streams["terminal"]
        os.replace(temporary, self.sample_path)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"重复 JSON 字段：{key}")
        result[key] = value
    return result


def _number(text):
    value = float(text)
    finite_number(value, "JSON 数值", minimum=-float("inf"))
    return value


def _read_lines(path):
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("文件超出大小上限")
    with path.open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            if len(line.encode("utf-8")) > MAX_LINE_BYTES:
                raise ValueError(f"第 {number} 行超出大小上限")
            if not line.strip():
                continue
            try:
                record = json.loads(line, object_pairs_hook=_pairs, parse_float=_number,
                                    parse_constant=lambda text: (_ for _ in ()).throw(ValueError("非有限 JSON 数值")))
                if not isinstance(record, dict):
                    raise ValueError("记录必须是对象")
                yield record
            except (ValueError, RecursionError) as error:
                raise ValueError(f"第 {number} 行：记录无效") from error


def _legacy_sample(record, index):
    series = record.get("series") or {}
    metrics = {key: record.get(key) for key in ("physical", "commit", "cache", "rates", "cpu", "tree", "commit_residual",
                                               "host", "codex_tree", "top_owners", "local", "age_buckets", "age_kinds",
                                               "private_sum_gib", "born_since_prev", "exited_since_prev", "kind", "count", "interval_s")}
    processes = record.get("processes") or []
    if isinstance(processes, dict):
        processes = processes.get("top") or []
    if not isinstance(processes, list):
        metrics["processes"] = processes
        processes = []
    return Sample(series.get("run_id") or record.get("run_id") or "legacy", series.get("index", record.get("index", index)),
                  record.get("started_at_utc") or record.get("timestamp") or record.get("at_utc") or record.get("utc"),
                  metrics, processes, [{"reason": "历史记录缺少完整运行条件"}])


def _load_run(path):
    path = Path(path)
    is_directory = path.is_dir()
    sample_path = path / "samples.jsonl" if is_directory else path
    result = {"samples": [], "header": None, "terminal": None, "analysis": None, "action": {}, "gaps": []}
    seen_end = False
    for record in _read_lines(sample_path):
        kind = record.get("record_type")
        if seen_end:
            raise ValueError("结束记录后存在多余数据")
        if kind in ("sample", "analysis", "end") and result["header"] is not None and record.get("run_id") != result["header"].get("run_id"):
            raise ValueError("记录不属于该运行")
        if kind == "header":
            if (result["header"] is not None or result["samples"]
                    or type(record.get("schema_version")) is not int or record["schema_version"] != SCHEMA_VERSION
                    or type(record.get("expected_count")) is not int or not 1 <= record["expected_count"] <= MAX_SAMPLES
                    or not isinstance(record.get("parameters"), dict)):
                raise ValueError("运行头重复、顺序或版本错误")
            result["header"] = record
            for key in ("program_version", "rule_version", "build_id", "environment_id", "started_at", "mode"):
                if not isinstance(record.get(key), str) or not record[key]:
                    result["gaps"].append(f"缺少运行元数据 {key}")
            try:
                finite_number(record.get("interval_s"), "interval_s")
                stamp = datetime.fromisoformat(record.get("started_at", "").replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError("开始时间缺时区")
            except (ValueError, TypeError):
                result["gaps"].append("运行间隔或开始时间无效")
        elif kind == "sample":
            if result["header"] is None:
                raise ValueError("样本缺少运行头")
            sample = Sample(**record["sample"])
            if sample.timestamp is None:
                result["gaps"].append("新版样本缺少时刻")
            if sample.run_id != result["header"].get("run_id") or record.get("run_id") != sample.run_id:
                raise ValueError("混合运行记录")
            if sample.sequence != len(result["samples"]):
                result["gaps"].append("缺样或重复序号")
            result["samples"].append(sample)
        elif kind == "analysis":
            if result["analysis"] is not None or not isinstance(record.get("analysis"), dict):
                raise ValueError("分析结果无效或重复")
            result["analysis"] = record["analysis"]
        elif kind == "end":
            result["terminal"] = record
            seen_end = True
        elif kind is None:
            if result["header"] is not None:
                raise ValueError("不能混合历史与新版记录")
            result["samples"].append(_legacy_sample(record, len(result["samples"])))
        else:
            raise ValueError("未知记录类型")
        if len(result["samples"]) > MAX_SAMPLES:
            raise ValueError("样本数量超出上限")
    if not result["samples"]:
        raise ValueError("没有可用样本")
    if len({s.run_id for s in result["samples"]}) != 1:
        raise ValueError("不能混合运行")
    if result["header"] is None or result["terminal"] is None:
        result["gaps"].append("运行元数据不完整")
    else:
        header, terminal = result["header"], result["terminal"]
        if (terminal.get("run_id") != header.get("run_id") or type(terminal.get("sample_count")) is not int
                or terminal["sample_count"] != len(result["samples"])):
            raise ValueError("终态与样本不一致")
        if header.get("expected_count") != len(result["samples"]) or terminal.get("completion") != "success":
            result["gaps"].append("运行未完整成功")
    operation_path = sample_path.parent / "operation.jsonl"
    if operation_path.exists():
        action_phase = None
        for record in _read_lines(operation_path):
            if record.get("run_id") != result["samples"][0].run_id:
                raise ValueError("动作记录不属于该运行")
            if record.get("record_type") == "intent":
                if action_phase is not None:
                    raise ValueError("重复或乱序动作意图")
                action_phase = "intent"
                result["action"] = {"attempted": None, "call_outcome": "unknown"}
            elif record.get("record_type") == "action_result":
                if action_phase != "intent" or not isinstance(record.get("action"), dict):
                    raise ValueError("动作结果缺少意图或重复")
                action_phase = "result"
                result["action"] = validate_action(record["action"])
                if (result["action"].get("call_outcome") != "success"
                        or result["action"].get("privilege_restored") is not True
                        or result["action"].get("errors")):
                    result["gaps"].append("维护运行未完整成功或恢复未确认")
            else:
                raise ValueError("未知动作记录类型")
        if result["action"].get("call_outcome") == "unknown":
            result["gaps"].append("历史动作结果未知，不重放")
    header = result["header"] or {}
    if (header.get("mode") == "maintain" or header.get("mechanism")) and not result["action"]:
        result["action"] = {"attempted": None, "call_outcome": "unknown", "privilege_restored": None}
        result["gaps"].append("维护动作记录缺失，结果未知，不重放")
    return result


class Application:
    def __init__(self, reader=None, maintainer=None, *, sleep=None, clock=_now):
        self.reader = reader
        self.maintainer = maintainer
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._active = False
        self._dispatched = False
        self._unsafe = False
        self._closed = False
        self._pending_resources = []

    @property
    def busy(self):
        with self._lock:
            return self._active

    def request_cancel(self):
        with self._lock:
            if not self._active:
                return False
            self._cancel.set()
            return not self._dispatched

    def shutdown(self):
        with self._lock:
            self._closed = True
            self._cancel.set()

    def _begin(self, report):
        with self._lock:
            if self._closed or self._active or (report.mode != "review" and self._unsafe):
                _error(report, "admission", ValueError("当前忙碌或安全上下文未恢复，拒绝新请求"))
                return False
            self._active = True
            self._dispatched = False
            self._cancel.clear()
            return True

    def _report(self, mode):
        return RunReport(uuid.uuid4().hex, mode)

    def _header(self, report, options):
        digest = hashlib.sha256()
        for path in sorted(Path(__file__).parent.glob("*.py")):
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
        return {"record_type": "header", "schema_version": SCHEMA_VERSION,
                "program_version": PROGRAM_VERSION, "rule_version": RULE_VERSION,
                "build_id": digest.hexdigest(),
                "environment_id": f"{platform.system()}-{platform.version()}-python{platform.python_version()}",
                "environment": {"system": platform.system(), "version": platform.version(),
                                "python": platform.python_version()},
                "run_id": report.run_id, "started_at": self._clock(),
                "mode": report.mode,
                "parameters": {k: v for k, v in options.items() if k not in ("output_dir", "jsonl_path")},
                "expected_count": options["count"], "interval_s": options["interval"]}

    def _read(self, session, report, journal):
        sample = session.read(report.run_id, len(report.samples))
        if (not isinstance(sample, Sample) or sample.run_id != report.run_id
                or sample.sequence != len(report.samples) or sample.timestamp is None):
            raise ValueError("Reader 返回的样本身份无效")
        # 再检查可变数据，不能只相信构造时的校验。
        Sample(**sample.to_dict())
        if len(_json(sample.to_dict()).encode("utf-8")) > MAX_LINE_BYTES:
            raise ValueError("样本超出大小上限")
        report.samples.append(sample)
        if journal:
            journal.append("samples", {"record_type": "sample", "sample": sample.to_dict()})
        return sample

    def _close(self, resource, report):
        if resource is None:
            return
        try:
            facts = resource.close()
            if not isinstance(facts, dict) or facts.get("closed") is not True or facts.get("errors"):
                raise ValueError("资源关闭未确认")
        except Exception as error:
            self._unsafe = True
            self._pending_resources.append(resource)
            _error(report, "close", error)

    def _completion(self, report):
        outcome = report.action.get("call_outcome")
        if outcome == "unknown":
            return "unknown"
        if report.errors:
            return "partial" if report.action.get("attempted") is True else "failed"
        if outcome == "failure":
            return "failed"
        if self._cancel.is_set() and not self._dispatched:
            return "cancelled"
        if report.mode == "maintain" and outcome == "not_started":
            return "failed"
        return "success"

    def _finish(self, report, journal):
        report.cancel_requested = self._cancel.is_set()
        report.completion = self._completion(report)
        report.phase = "finished"
        terminal = {"record_type": "end", "completion": report.completion,
                    "sample_count": len(report.samples), "finished_at": self._clock(),
                    "cancel_requested": report.cancel_requested, "errors": report.errors.copy()}
        report.metadata["terminal"] = terminal
        try:
            if journal:
                close_errors = journal.close()
                if close_errors:
                    self._unsafe = True
                    self._pending_resources.append(journal)
                    for error in close_errors:
                        _error(report, "record_close", error)
                else:
                    journal.commit_end(terminal)
        except Exception as error:
            _error(report, "terminal_record", error)
        finally:
            if journal and journal.streams:
                self._unsafe = True
                self._pending_resources.append(journal)
                for error in journal.close():
                    _error(report, "record_close", error)
            report.completion = self._completion(report)
            report.metadata["terminal"]["completion"] = report.completion
            report.metadata["terminal"]["errors"] = report.errors.copy()
            with self._lock:
                self._active = False

    def inspect(self, options=None):
        report = self._report("inspect")
        if not self._begin(report):
            return report
        journal = session = None
        try:
            options = _options(options)
            report.metadata["header"] = self._header(report, options)
            if options.get("output_dir") is not None or options.get("jsonl_path") is not None:
                journal = _Journal(report, options)
                journal.append("samples", report.metadata["header"])
            if self.reader is None:
                raise ValueError("缺少读取接入")
            session = self.reader.open_observation(with_processes=options["with_processes"], top=options["top"])
            report.phase = "reading"
            for index in range(options["count"]):
                if self._cancel.is_set():
                    break
                self._read(session, report, journal)
                if index + 1 < options["count"]:
                    if self._sleep is None:
                        self._cancel.wait(options["interval"])
                    else:
                        self._sleep(options["interval"])
            report.analysis = rules.analyze(report.samples, conditions=options["conditions"])
            if journal:
                journal.append("samples", {"record_type": "analysis", "analysis": report.analysis})
        except Exception as error:
            _error(report, report.phase, error)
        finally:
            report.phase = "closing"
            self._close(session, report)
            self._finish(report, journal)
        return report

    def maintain(self, request, output_dir):
        report = self._report("maintain")
        if not self._begin(report):
            return report
        journal = session = operation = None
        try:
            if not isinstance(request, ActionRequest) or not request.confirmed or not request.confirmation_source.strip():
                raise ValueError("维护必须有明确确认及来源")
            if output_dir is None or self.maintainer is None or self.reader is None:
                raise ValueError("维护必须提供输出目录和接入")
            options = _options({"count": 2, "interval": 0, "output_dir": output_dir})
            report.metadata["header"] = self._header(report, options)
            report.metadata["header"]["mechanism"] = request.mechanism
            journal = _Journal(report, options)
            journal.append("samples", report.metadata["header"])
            operation = self.maintainer.open_operation(request)
            report.phase = "preflight"
            preflight = operation.preflight()
            report.metadata["preflight"] = preflight
            if not isinstance(preflight, dict) or any(preflight.get(k) is not True for k in
                                                     ("supported", "privilege_available", "lock_acquired")) or preflight.get("scope") != request.scope:
                raise ValueError("维护准入能力缺失或未知")
            session = self.reader.open_observation(with_processes=False, top=10)
            report.phase = "before"
            before = self._read(session, report, journal)
            for block, key in (("physical", "available_bytes"), ("commit", "used_bytes"), ("commit", "limit_bytes")):
                finite_number((before.metrics.get(block) or {}).get(key), f"{block}.{key}")
            journal.append("operation", {"record_type": "intent", "mechanism": request.mechanism,
                                         "scope": request.scope, "confirmation_source": request.confirmation_source})
            with self._lock:
                accepted_cancel = self._cancel.is_set()
                if not accepted_cancel:
                    self._dispatched = True
                    report.phase = "calling"
            if accepted_cancel:
                journal.append("operation", {"record_type": "action_result", "action": report.action,
                                             "cancellation": "accepted_before_dispatch"})
                return report
            try:
                action = operation.perform_once()
                report.action = validate_action(action)
                if action.get("errors"):
                    _error(report, "calling", ValueError("维护或恢复存在错误，见动作事实"))
                if action.get("privilege_restored") is not True:
                    self._unsafe = True
                    _error(report, "restore", ValueError("安全上下文恢复未确认"))
            except Exception as error:
                if report.action.get("call_outcome") == "not_started":
                    report.action = {"attempted": None, "call_outcome": "unknown", "privilege_restored": None}
                self._unsafe = True
                _error(report, "calling", error)
            report.metadata["action"] = report.action.copy()
            report.phase = "action_record"
            journal.append("operation", {"record_type": "action_result", "action": report.action})
            report.phase = "after"
            self._read(session, report, journal)
            report.analysis = rules.analyze(report.samples)
            journal.append("samples", {"record_type": "analysis", "analysis": report.analysis})
        except Exception as error:
            _error(report, report.phase, error)
        finally:
            report.phase = "closing"
            self._close(session, report)
            self._close(operation, report)
            self._finish(report, journal)
        return report

    def review(self, path, comparison=None, *, conditions=None):
        report = self._report("review")
        if not self._begin(report):
            return report
        try:
            original = _load_run(path)
            report.samples = original["samples"]
            if original["action"]:
                report.action = original["action"].copy()
            report.metadata = {k: original[k] for k in ("header", "terminal", "action")}
            report.metadata["source_gaps"] = original["gaps"]
            if conditions is not None and not isinstance(conditions, dict):
                raise ValueError("conditions: 必须是对象")
            effective = conditions if conditions is not None else ((original["header"] or {}).get("parameters") or {}).get("conditions", {})
            if comparison is not None:
                other = _load_run(comparison)
                report.analysis = rules.compare(original["samples"], other["samples"], conditions=effective,
                                                metadata_a=original, metadata_b=other)
            elif conditions is None and original["analysis"] is not None:
                report.analysis = original["analysis"]
                report.metadata["recomputed"] = False
            else:
                report.analysis = rules.analyze(report.samples, conditions=effective)
                report.metadata["recomputed"] = True
                report.metadata["recomputed_rule_version"] = RULE_VERSION
            if original["gaps"]:
                _error(report, "source", ValueError("原运行条件或结果不完整，不能支持收益"))
            report.completion = "partial" if report.errors else "success"
            report.phase = "finished"
        except Exception as error:
            _error(report, "review", error)
            report.completion = "failed"
        finally:
            with self._lock:
                self._active = False
        return report
