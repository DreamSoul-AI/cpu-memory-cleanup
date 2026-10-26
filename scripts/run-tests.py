"""展开分层测试配置并隔离运行 unittest，保存逐次结果及可重建报告。"""

import argparse
import ast
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import ctypes
from datetime import datetime, timezone
import fnmatch
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time
import traceback
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
DIMENSIONS = {"level": {"unit", "integration", "e2e"},
              "type": {"location", "content", "physical"},
              "priority": {"p1", "p2", "p3"},
              "cost_class": {"c1", "c2", "c3", "c4"},
              "result_type": {"categorical", "numeric"},
              "result_detail": {"summary", "metrics", "samples"}}
FINAL_STATUSES = {"passed", "failed", "measured", "completed", "error", "skipped", "xfail", "xpass", "timeout"}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _read_json(path):
    if Path(path).stat().st_size > 16 * 1024 * 1024:
        raise ValueError("JSON 文件超出 16 MiB 上限")
    def invalid(value):
        raise ValueError(f"非法 JSON 常量：{value}")
    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("JSON 数值必须有限")
        return parsed
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=invalid, parse_float=number)


def _save_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(_json(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name}: 必须为有限正数")
    return value


def _strings(value, name):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{name}: 必须为字符串列表")
    return value


def load_catalog(path, root):
    """按目录继承测试元数据，静态发现方法后展开为现有执行合同。"""
    path, root = Path(path).resolve(), Path(root).resolve()
    catalog = _read_json(path)
    if not isinstance(catalog, dict):
        raise ValueError("测试清单必须是对象")
    if catalog.get("schema_version") != 2:
        if path == root / "tests" / "manifest.json" and any(
                config != path for config in path.parent.rglob("manifest.json")):
            raise ValueError("根清单仍为旧格式，但存在局部清单，不能静默忽略分层配置")
        return validate_catalog(catalog)
    if path != root / "tests" / "manifest.json":
        raise ValueError("树状清单必须从项目 tests/manifest.json 加载")
    allowed = {"target", "tags", "level", "type", "priority", "cost_class", "result_type",
               "result_detail", "timeout_s", "memory_bytes", "quality_assertion", "cost_estimate", "plans"}

    def merge(base, updates, *, cases=False, global_defaults=False):
        keys = allowed | ({"subtests", "depends_on", "rerun_safe"} if cases else set())
        if global_defaults:
            keys |= {"resources"}
        if not isinstance(updates, dict) or set(updates) - keys:
            raise ValueError("测试元数据包含无效字段或子目录不可覆盖的全局策略")
        result = dict(base)
        for key, value in updates.items():
            result[key] = {**result[key], **value} if isinstance(value, dict) and isinstance(result.get(key), dict) else value
        return result

    base = merge({}, catalog.get("defaults", {}), global_defaults=True)
    if not isinstance(catalog.get("plans"), dict) or not catalog["plans"]:
        raise ValueError("缺少全局测试计划")
    plans = {}
    for name, plan in catalog["plans"].items():
        if not isinstance(plan, dict) or plan.get("required") != "all" or "test_ids" in plan:
            raise ValueError("树状计划必须声明 required=all，由测试归属生成名单")
        plans[name] = {**plan, "test_ids": [], "required": []}
    scopes, config_files = {}, []
    for config in sorted(path.parent.rglob("manifest.json"), key=lambda item: (len(item.parts), item.as_posix())):
        if not config.resolve().is_relative_to(path.parent):
            raise ValueError("测试配置不能越出 tests 目录")
        data = catalog if config == path else _read_json(config)
        if config != path and (not isinstance(data, dict) or set(data) - {"defaults", "files"}):
            raise ValueError("子目录清单只允许 defaults 和 files")
        inherited = base
        for ancestor in reversed(config.parent.parents):
            if ancestor in scopes:
                inherited = scopes[ancestor]["defaults"]
        defaults = inherited if config == path else merge(inherited, data.get("defaults", {}))
        files = data.get("files", {})
        if not isinstance(files, dict):
            raise ValueError("files 必须是当前目录测试文件的映射")
        for filename, settings in files.items():
            if (not isinstance(filename, str) or Path(filename).name != filename
                    or not filename.startswith("test_") or not filename.endswith(".py")
                    or not (config.parent / filename).is_file() or not isinstance(settings, dict)):
                raise ValueError("files 只能配置当前目录存在的 test_*.py")
        scopes[config.parent] = {"defaults": defaults, "files": files}
        config_files.append({"path": config.relative_to(root).as_posix(),
                             "sha256": hashlib.sha256(config.read_bytes()).hexdigest()})
    entries, sources = [], {}
    for test_file in sorted(path.parent.rglob("test_*.py")):
        if not test_file.resolve().is_relative_to(path.parent):
            raise ValueError("测试源文件不能越出 tests 目录")
        defaults, origins = base, [path.relative_to(root).as_posix()]
        for folder in [*reversed(test_file.parent.parents), test_file.parent]:
            if folder in scopes:
                defaults = scopes[folder]["defaults"]
                config_name = (folder / "manifest.json").relative_to(root).as_posix()
                if config_name not in origins:
                    origins.append(config_name)
        settings = scopes.get(test_file.parent, {}).get("files", {}).get(test_file.name, {})
        overrides = settings.get("cases", {})
        if not isinstance(overrides, dict):
            raise ValueError("cases 必须是方法配置映射")
        defaults = merge(defaults, {k: v for k, v in settings.items() if k != "cases"})
        tree = ast.parse(test_file.read_text(encoding="utf-8-sig"), filename=str(test_file))
        # shortcut: 只发现类内直接定义的方法，采用继承或动态用例前扩展发现合同。
        methods = [node.name + "." + method.name for node in tree.body if isinstance(node, ast.ClassDef)
                   for method in node.body if isinstance(method, ast.FunctionDef) and method.name.startswith("test_")]
        if set(overrides) - set(methods):
            raise ValueError(f"{test_file.name}: cases 引用了不存在的方法")
        relative = test_file.relative_to(root).as_posix()
        for method in methods:
            entry = merge(defaults, overrides.get(method, {}), cases=True)
            memberships = _strings(entry.pop("plans", []), "plans")
            if not memberships or set(memberships) - set(plans):
                raise ValueError("测试必须归属已定义的全局执行计划")
            identity = relative + "::" + method
            entry.update(test_id=identity, file=relative,
                         unittest_id=relative[:-3].replace("/", ".") + "." + method)
            entries.append(entry)
            sources[identity] = origins
            for name in memberships:
                plans[name]["test_ids"].append(identity)
                plans[name]["required"].append(identity)
    return validate_catalog({**catalog, "schema_version": 1, "defaults": {}, "tests": entries,
                             "plans": plans, "config_sources": sources, "config_files": config_files})


def validate_catalog(catalog):
    if type(catalog.get("schema_version")) is not int or catalog["schema_version"] != 1 or not isinstance(catalog.get("tests"), list):
        raise ValueError("测试清单版本或结构无效")
    defaults, file_defaults = catalog.get("defaults", {}), catalog.get("file_defaults", {})
    if not isinstance(defaults, dict) or not isinstance(file_defaults, dict):
        raise ValueError("默认元数据必须是对象")
    expanded = []
    for entry in catalog["tests"]:
        if not isinstance(entry, dict):
            raise ValueError("测试条目必须是对象")
        effective = {}
        for source in (defaults, file_defaults.get(entry.get("file"), {}), entry):
            if not isinstance(source, dict):
                raise ValueError("文件默认元数据必须是对象")
            for key, value in source.items():
                effective[key] = {**effective[key], **value} if isinstance(value, dict) and isinstance(effective.get(key), dict) else value
        expanded.append(effective)
    catalog = {**catalog, "tests": expanded}
    seen = set()
    for entry in catalog["tests"]:
        if not isinstance(entry, dict):
            raise ValueError("测试条目必须是对象")
        for key in ("test_id", "unittest_id", "file", "target"):
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise ValueError(f"测试缺少 {key}")
        if entry["test_id"] in seen:
            raise ValueError("重复 test_id")
        seen.add(entry["test_id"])
        if not re.fullmatch(r"tests(?:\.[A-Za-z_]\w*){3,}", entry["unittest_id"]):
            raise ValueError("unittest_id 必须定位 tests 包下的一个方法")
        for key, allowed in DIMENSIONS.items():
            if entry.get(key) not in allowed:
                raise ValueError(f"{entry['test_id']}: {key} 无效")
        if entry["type"] == "location" and entry["level"] != "unit":
            raise ValueError("location 只能属于 unit")
        if entry["result_type"] == "numeric" and entry["result_detail"] == "summary":
            raise ValueError("numeric 至少保留 metrics")
        tags = _strings(entry.get("tags"), "tags")
        if any(not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", tag) for tag in tags):
            raise ValueError("标签必须使用英文小写 snake_case")
        for key in ("level", "type", "priority"):
            if set(tags) & DIMENSIONS[key] - {entry[key]}:
                raise ValueError("标签与互斥维度冲突")
        _strings(entry.get("resources"), "resources")
        _strings(entry.get("depends_on", []), "depends_on")
        _positive(entry.get("timeout_s"), "timeout_s")
        memory = entry.get("memory_bytes")
        if type(memory) is not int or memory <= 0:
            raise ValueError("memory_bytes 必须为正整数")
        if type(entry.get("quality_assertion", True)) is not bool:
            raise ValueError("quality_assertion 必须是布尔值")
        limits = catalog.get("cost_classes", {}).get(entry["cost_class"])
        if not isinstance(limits, dict):
            raise ValueError("成本等级缺少项目预算依据")
        if entry["timeout_s"] > _positive(limits.get("timeout_s"), "cost.timeout_s") or memory > _positive(limits.get("memory_bytes"), "cost.memory_bytes"):
            raise ValueError("用例预算超出其成本等级")
    for entry in catalog["tests"]:
        if set(entry.get("depends_on", [])) - seen:
            raise ValueError("用例依赖不存在")
    if not isinstance(catalog.get("plans"), dict) or not catalog["plans"]:
        raise ValueError("缺少测试计划")
    for name, plan in catalog["plans"].items():
        if not isinstance(plan, dict) or plan.get("batch_cost_class") not in DIMENSIONS["cost_class"]:
            raise ValueError(f"{name}: 批次等级无效")
        for key in ("test_ids", "required", "resources", "dependencies"):
            _strings(plan.get(key, []), key)
        if set(plan.get("test_ids", [])) - seen or set(plan.get("required", [])) - seen:
            raise ValueError("计划引用未知测试")
        budget = plan.get("budget", {})
        _positive(budget.get("wall_seconds"), "batch.wall_seconds")
        if type(budget.get("memory_bytes")) is not int or budget["memory_bytes"] <= 0:
            raise ValueError("批次内存上限无效")
    return catalog


def _plan(catalog, name, visiting=None):
    visiting = set() if visiting is None else visiting
    if name in visiting or name not in catalog["plans"]:
        raise ValueError("计划依赖不存在或成环")
    visiting.add(name)
    result = dict(catalog["plans"][name])
    ids, required, stages = [], [], []
    for dependency in result.get("dependencies", []):
        earlier = _plan(catalog, dependency, visiting)
        ids.extend(earlier["test_ids"])
        required.extend(earlier["required"])
        stages.extend(stage for stage in earlier["stages"]
                      if stage["plan_id"] not in {previous["plan_id"] for previous in stages})
    visiting.remove(name)
    stages.append({"plan_id": name, "test_ids": [identity for identity in result.get("test_ids", []) if identity not in ids],
                   "required": list(dict.fromkeys(required + result.get("required", []))),
                   "dependencies": result.get("dependencies", [])})
    result["test_ids"] = list(dict.fromkeys(ids + result.get("test_ids", [])))
    result["required"] = list(dict.fromkeys(required + result.get("required", [])))
    result["stages"] = stages
    return result


def select_tests(catalog, args):
    plan = _plan(catalog, args.plan)
    selected, excluded = [], {}
    for entry in catalog["tests"]:
        reasons = []
        if not args.all and entry["test_id"] not in plan["test_ids"]:
            reasons.append("不属于计划")
        for key in DIMENSIONS:
            wanted = getattr(args, key, None)
            if wanted and entry[key] not in wanted:
                reasons.append(key)
        if args.tag and not set(args.tag).issubset(set(entry["tags"]) | {entry["level"], entry["type"], entry["priority"]}):
            reasons.append("tag")
        for key, field in (("file", "file"), ("method", "unittest_id"), ("target", "target"), ("test_id", "test_id")):
            patterns = getattr(args, key, None)
            if patterns and not any(fnmatch.fnmatchcase(entry[field], pattern) or pattern in entry[field] for pattern in patterns):
                reasons.append(key)
        if args.resource and not set(args.resource).issubset(entry["resources"]):
            reasons.append("resource")
        estimate = entry.get("cost_estimate", {}).get("estimated_wall_time_ms")
        if args.max_estimated_ms is not None and (not isinstance(estimate, (int, float))
                                                 or isinstance(estimate, bool) or estimate > args.max_estimated_ms):
            reasons.append("预计成本缺失或超出筛选条件")
        if reasons:
            excluded[entry["test_id"]] = reasons
        else:
            selected.append(entry)
    if args.all:
        plan["required"] = list(dict.fromkeys(plan["required"] + [entry["test_id"] for entry in selected]))
    ordered, remaining = [], selected.copy()
    ids = {entry["test_id"] for entry in selected}
    while remaining:
        ready = [entry for entry in remaining if not (set(entry.get("depends_on", [])) & ids
                                                    - {item["test_id"] for item in ordered})]
        if not ready:
            raise ValueError("用例依赖成环")
        def stage_index(entry):
            return next((index for index, stage in enumerate(plan["stages"]) if entry["test_id"] in stage["test_ids"]), len(plan["stages"]) - 1)
        entry = min(ready, key=lambda item: (stage_index(item), item["priority"], item["cost_class"], item["test_id"]))
        ordered.append(entry)
        remaining.remove(entry)
    return plan, ordered, excluded


def _safe_error(error, root):
    message = "".join(traceback.format_exception(type(error), error, error.__traceback__))[-12000:]
    return _redact(message, root)


def _redact(message, root):
    for value, replacement in ((str(root), "<repo>"), (str(Path.home()), "<home>")):
        message = message.replace(value, replacement)
    return re.sub(r"(?i)(password|token|secret|authorization)(\s*[:=]\s*)[^\s,;]+", r"\1\2<redacted>", message)


def _metrics(value, detail):
    if not isinstance(value, list):
        raise ValueError("metrics 必须是列表")
    for metric in value:
        if not isinstance(metric, dict) or any(not metric.get(key) for key in ("name", "unit", "statistic")):
            raise ValueError("数值指标缺少名称、单位或统计口径")
        number = metric.get("value")
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            raise ValueError("数值指标必须有限")
        if type(metric.get("samples")) is not int or metric["samples"] < 1:
            raise ValueError("指标缺少实际样本数")
        if type(metric.get("warmup_iterations")) is not int or metric["warmup_iterations"] < 0:
            raise ValueError("指标缺少预热说明")
        if not isinstance(metric.get("context"), dict) or "data_size" not in metric["context"]:
            raise ValueError("指标缺少数据规模和测量条件")
        if detail == "samples" and "raw_samples" not in metric and "raw_samples_artifact" not in metric:
            raise ValueError("samples 粒度缺少原始样本")
    _json(value)
    return value


class _Result(unittest.TestResult):
    def __init__(self, entry, root, progress_path=None):
        super().__init__()
        self.entry, self.root = entry, root
        self.problems, self.subtests = [], []
        self.status = None
        self.assertions = 0
        self.case = None
        self.phase = "fixture"
        self.sub_contexts = []
        self.progress_path = progress_path

    def checkpoint(self, value):
        if self.progress_path:
            with Path(self.progress_path).open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(_json(value) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def startTest(self, test):
        super().startTest(test)
        self.case = test

    def _problem(self, error, phase=None):
        value = error[1]
        self.problems.append({"phase": phase or self.phase, "type": type(value).__name__,
                              "traceback": _safe_error(value, self.root)})
        self.checkpoint({"kind": "failure", "failure": self.problems[-1]})

    def stopTest(self, test):
        super().stopTest(test)
        self.checkpoint({"kind": "case", "metrics": getattr(test, "metrics", []), "failure": self.problems})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._problem(err)
        self.status = "failed" if self.phase == "test" else "error"

    def addError(self, test, err):
        super().addError(test, err)
        self._problem(err, "fixture" if isinstance(test, unittest.suite._ErrorHolder) else None)
        self.status = "error"

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.problems.append({"phase": self.phase, "type": "SkipTest", "message": str(reason)})
        self.status = "skipped"

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._problem(err)
        self.status = "xfail"

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.status = "xpass"

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        parameters = dict(subtest.params)
        suffix = _json(parameters)
        started, at, assertions = self.sub_contexts[-1] if self.sub_contexts else (time.monotonic(), _now(), self.assertions)
        item = {"parameters": parameters, "suffix": suffix,
                "status": "passed" if err is None else "failed" if issubclass(err[0], test.failureException) else "error",
                "failure": None if err is None else [{"phase": "subtest", "type": err[0].__name__, "traceback": _safe_error(err[1], self.root)}],
                "duration_ms": (time.monotonic() - started) * 1000, "started_at": at,
                "metrics": [], "verdict": "pass" if err is None else "fail" if issubclass(err[0], test.failureException) else None,
                "category": "pass" if err is None else "fail" if issubclass(err[0], test.failureException) else None}
        try:
            item["metrics"] = _metrics(getattr(test, "metrics", []), self.entry["result_detail"])
            if err is None:
                if self.entry["result_type"] == "numeric":
                    if not item["metrics"]:
                        raise ValueError("参数测量没有有效指标")
                    item.update(status="measured", verdict=None, category=None)
                elif self.entry.get("quality_assertion", True) and self.assertions == assertions:
                    raise ValueError("参数用例没有实际断言")
                elif not self.entry.get("quality_assertion", True):
                    if not self.entry.get("category_rule") or not getattr(test, "category", None):
                        raise ValueError("参数分类缺少类别规则")
                    item.update(status="completed", verdict=None, category=test.category)
        except Exception as error:
            item.update(status="error", verdict=None, category=None,
                        failure=[{"phase": "subtest", "type": type(error).__name__, "traceback": _safe_error(error, self.root)}])
        self.subtests.append(item)
        self.checkpoint({"kind": "subtest", "result": item})
        if item["status"] == "error":
            self.status = "error"
        if err is not None:
            self._problem(err, "subtest")
            self.status = "failed" if issubclass(err[0], test.failureException) else "error"


def _run_case(entry, root, progress_path=None):
    result = _Result(entry, root, progress_path)
    original = {}
    for name in dir(unittest.TestCase):
        method = getattr(unittest.TestCase, name)
        if name.startswith("assert") and callable(method):
            original[name] = method
            def counted(self, *args, _method=method, **kwargs):
                result.assertions += 1
                return _method(self, *args, **kwargs)
            setattr(unittest.TestCase, name, counted)
    phase_methods = {"_callSetUp": "setup", "_callTestMethod": "test", "_callTearDown": "teardown", "doCleanups": "cleanup"}
    for name, phase in phase_methods.items():
        method = getattr(unittest.TestCase, name)
        original[name] = method
        def marked(self, *args, _method=method, _phase=phase, **kwargs):
            result.phase = _phase
            return _method(self, *args, **kwargs)
        setattr(unittest.TestCase, name, marked)
    original["subTest"] = unittest.TestCase.subTest
    @contextmanager
    def parameter_case(self, *args, **kwargs):
        result.sub_contexts.append((time.monotonic(), _now(), result.assertions))
        try:
            with original["subTest"](self, *args, **kwargs):
                yield
        finally:
            result.sub_contexts.pop()
    unittest.TestCase.subTest = parameter_case
    captured = io.StringIO()
    metrics, category, verdict = [], None, None
    try:
        with redirect_stdout(captured), redirect_stderr(captured):
            suite = unittest.TestLoader().loadTestsFromName(entry["unittest_id"])
            if suite.countTestCases() != 1:
                raise ValueError("unittest_id 必须恰好选择一个用例")
            suite.run(result)
        status = result.status
        if status is None:
            metrics = _metrics(getattr(result.case, "metrics", []), entry["result_detail"])
            if entry["result_type"] == "numeric":
                if not metrics:
                    raise ValueError("numeric 用例没有测量结果")
                status = "measured"
            elif entry.get("quality_assertion", True):
                if not result.assertions:
                    raise ValueError("判定型用例没有实际 unittest 断言")
                status, category, verdict = "passed", "pass", "pass"
            else:
                category = getattr(result.case, "category", None)
                if not isinstance(category, str) or not category or not entry.get("category_rule"):
                    raise ValueError("分类用例缺少有效类别或分类规则")
                status = "completed"
                mapping = entry.get("quality_mapping")
                if mapping is not None:
                    if not entry.get("quality_mapping_source") or mapping.get(category) not in ("pass", "fail"):
                        raise ValueError("类别质量映射缺少依据或没有对应类别")
                    verdict = mapping[category]
                    status = "passed" if verdict == "pass" else "failed"
        elif status == "failed":
            category, verdict = "fail", "fail"
        if result.case is not None and not metrics:
            metrics = _metrics(getattr(result.case, "metrics", []), entry["result_detail"])
        if status in ("xfail", "xpass") and not entry.get("known_defect"):
            raise ValueError("预期失败没有已知缺陷依据")
        declared = entry.get("subtests", [])
        actual = [item["parameters"] for item in result.subtests]
        if actual and actual != declared:
            raise ValueError("subTest 参数未在清单完整展开或与声明不一致")
        return {"status": status, "verdict": verdict, "category": category,
                "metrics": metrics, "failure": result.problems or None,
                "subtests": result.subtests, "assertion_count": result.assertions,
                "captured": captured.getvalue()[-16000:]}
    except BaseException as error:
        result.problems.append({"phase": result.phase, "type": type(error).__name__,
                                "traceback": _safe_error(error, root)})
        return {"status": "error", "verdict": None, "category": None, "metrics": metrics,
                "failure": result.problems, "subtests": result.subtests,
                "captured": captured.getvalue()[-16000:]}
    finally:
        for name, method in original.items():
            setattr(unittest.TestCase, name, method)


def _worker():
    # 父进程先把本进程加入自己的 Job，再经 stdin 允许导入测试。
    payload = json.loads(sys.stdin.readline())
    root = Path(payload["root"]).resolve()
    if os.name != "nt":
        import resource
        memory = payload["memory_bytes"]
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    sys.path[:0] = [str(root / "src"), str(root)]
    result = _run_case(payload["entry"], root, payload.get("progress_path"))
    print(_json(result), flush=True)
    return 0


class _WindowsJob:
    """只持有本入口新建的匿名 Job；内存口径是提交量，不是 RSS。"""
    def __init__(self, memory_bytes):
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("minimum_ws", ctypes.c_size_t),
                        ("maximum_ws", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops", "read", "write", "other")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", Io), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        self.Extended = Extended
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {"CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
                      "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
                      "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
                      "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
                      "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
                      "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL)}
        for name, (args, restype) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, restype
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x100 | 0x200 | 0x2000
        limits.process_memory = limits.job_memory = memory_bytes
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def peak(self):
        value = self.Extended()
        if self.kernel.QueryInformationJobObject(self.handle, 9, ctypes.byref(value), ctypes.sizeof(value), None):
            return value.peak_job
        return None

    def terminate(self):
        if not self.kernel.TerminateJobObject(self.handle, 124):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            if not self.kernel.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


def _execute(entry, root, timeout_s, memory_bytes, progress_path=None):
    start, started_at = time.monotonic(), _now()
    job = _WindowsJob(memory_bytes) if os.name == "nt" else None
    process = None
    result, output, stderr, peak = None, "", "", None
    cleanup_errors = []
    try:
        process = subprocess.Popen([sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()), "--_worker"],
                                   cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace",
                                   creationflags=0x08000000 if os.name == "nt" else 0,
                                   start_new_session=os.name != "nt")
        if job:
            job.assign(process)
        payload = _json({"root": str(root), "entry": entry, "memory_bytes": memory_bytes,
                         "progress_path": str(progress_path) if progress_path else None}) + "\n"
        remaining = timeout_s - (time.monotonic() - start)
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout_s)
        output, stderr = process.communicate(payload, timeout=remaining)
        if process.returncode != 0:
            raise RuntimeError(f"测试工作进程非零退出：{process.returncode}")
        result = json.loads(output)
        if result.get("status") not in FINAL_STATUSES:
            raise ValueError("工作进程结果状态无效")
    except subprocess.TimeoutExpired:
        result = {"status": "timeout", "verdict": None, "category": None, "metrics": [],
                  "failure": [{"phase": "budget", "type": "Timeout", "message": "自建测试工作单元超出墙钟预算"}], "subtests": []}
        try:
            if job:
                job.terminate()
            elif process:
                os.killpg(process.pid, signal.SIGKILL)
            if process:
                output, stderr = process.communicate(timeout=5)
        except Exception as error:
            cleanup_errors.append(("timeout_stop", error))
    except Exception as error:
        result = {"status": "error", "verdict": None, "category": None, "metrics": [],
                  "failure": [{"phase": "worker", "type": type(error).__name__, "traceback": _safe_error(error, root)}], "subtests": []}
    finally:
        if job:
            try:
                peak = job.peak()
            except Exception as error:
                cleanup_errors.append(("job_peak", error))
            try:
                job.close()  # 只收回本 Job 中仍存在的测试子孙，不查找或接管其它 PID。
            except Exception as error:
                cleanup_errors.append(("job_close", error))
                try:
                    job.terminate()
                except Exception as error:
                    cleanup_errors.append(("job_terminate", error))
                try:
                    job.close()
                except Exception as error:
                    cleanup_errors.append(("job_close_retry", error))
        if process and process.poll() is None:
            try:
                process.kill()
            except Exception as error:
                cleanup_errors.append(("worker_kill", error))
            try:
                process.wait(timeout=5)
            except Exception as error:
                cleanup_errors.append(("worker_wait", error))
        if cleanup_errors and result is None:
            for operation, error in cleanup_errors:
                print(f"测试收尾失败 ({operation}): {type(error).__name__}", file=sys.stderr)
    if cleanup_errors:
        result.update(status="timeout" if result["status"] == "timeout" else "error", verdict=None, category=None)
        result["failure"] = [*(result.get("failure") or []),
                             *({"phase": "cleanup", "operation": operation, "type": type(error).__name__,
                                "traceback": _safe_error(error, root)} for operation, error in cleanup_errors)]
    elapsed = time.monotonic() - start
    result.update(duration_ms=elapsed * 1000, started_at=started_at,
                  peak_memory_bytes=peak, worker_stderr=stderr[-16000:])
    if elapsed > timeout_s and result["status"] != "timeout":
        result.update(status="timeout", verdict=None, category=None)
        result["failure"] = [*(result.get("failure") or []),
                             {"phase": "budget", "type": "Timeout", "message": "自建测试工作单元收尾后超出墙钟预算"}]
    if progress_path and Path(progress_path).exists() and result["status"] in ("timeout", "error"):
        observations = []
        with Path(progress_path).open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    observations.append(json.loads(line))
                except ValueError:
                    break
        result["partial_observations"] = observations
        if not result.get("subtests"):
            result["subtests"] = [item["result"] for item in observations if item["kind"] == "subtest"]
        if not result.get("metrics"):
            result["metrics"] = next((item.get("metrics", []) for item in reversed(observations) if item["kind"] == "case"), [])
    return result


def _sub_id(identity, parameters):
    return identity + "[" + json.dumps(parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "]"


def _expanded(entries):
    result = []
    for entry in entries:
        if entry.get("subtests"):
            result.extend({**entry, "test_id": _sub_id(entry["test_id"], parameters), "parameters": parameters}
                          for parameters in entry["subtests"])
        else:
            result.append(entry)
    return result


def _source_info(root, catalog):
    digest = hashlib.sha256()
    paths = {Path(__file__).resolve()}
    for folder in ("src", "scripts", "tests"):
        paths.update(path for path in (root / folder).rglob("*")
                     if path.is_file() and path.suffix.lower() in (".py", ".pyw", ".cmd", ".ps1"))
    if (root / "pyproject.toml").is_file():
        paths.add(root / "pyproject.toml")
    for path in sorted(paths):
        digest.update(str(path.relative_to(root) if path.is_relative_to(root) else "<runner>").encode("utf-8"))
        digest.update(path.read_bytes())
    digest.update(_json(catalog).encode("utf-8"))
    info = {"fingerprint": digest.hexdigest(), "commit": None, "dirty": None}
    try:
        info["commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL, timeout=3).strip()
        info["dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, text=True, stderr=subprocess.DEVNULL, timeout=3).strip())
    except (OSError, subprocess.SubprocessError):
        info["unavailable_reason"] = "当前目标不在可读取的 Git 仓库中"
    return info


def _events(directory):
    path = directory / "events.jsonl"
    if not path.exists():
        return []
    result = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError as error:
                raise ValueError(f"结果第 {number} 行损坏，不能当作完整运行") from error
            if value.get("status") not in FINAL_STATUSES:
                raise ValueError("结果状态无效")
            result.append(value)
    return result


def _append_event(directory, event):
    with (directory / "events.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(_json(event) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _acceptable(event, entry, plan):
    status = event.get("status") if event else None
    if status in ("passed", "measured", "completed"):
        return True
    if status == "skipped":
        return bool(entry.get("allow_skip") and plan.get("allow_skip"))
    if status == "xfail":
        return bool(entry.get("known_defect") and plan.get("allow_xfail"))
    return False


def rebuild_report(directory):
    directory = Path(directory)
    manifest = _read_json(directory / "manifest.json")
    events = _events(directory)
    latest, history = {}, {}
    for event in events:
        if event.get("run_id") != manifest["run_id"]:
            raise ValueError("结果运行标识与清单不一致")
        identity = event["test_id"]
        if type(event.get("attempt")) is not int or event["attempt"] < 1:
            raise ValueError("结果 attempt 无效")
        history.setdefault(identity, []).append(event)
        if identity not in latest or event["attempt"] >= latest[identity]["attempt"]:
            latest[identity] = event
    entries = {entry["test_id"]: entry for entry in _expanded(manifest["selected"])}
    plan = manifest["plan"]
    required = manifest["required"]
    required_groups = {entry["test_id"] for entry in manifest["selected"] if entry.get("subtests")
                       and any(child["test_id"] in required for child in _expanded([entry]))}
    blockers = []
    for identity in required:
        if identity not in entries or not _acceptable(latest.get(identity), entries.get(identity, {}), plan):
            blockers.append(identity)
    # 非必需项的真实失败同样不能被已选子集的通过掩盖。
    blockers.extend(identity for identity, event in latest.items()
                    if (identity in entries or event.get("record_type") == "group_result")
                    and event["status"] in ("failed", "error", "timeout", "xpass"))
    recovered = [identity for identity, records in history.items()
                 if len(records) > 1 and latest[identity]["status"] == "passed"
                 and any(record["status"] in ("failed", "error", "timeout") for record in records[:-1])]
    if not plan.get("allow_recovered", False):
        blockers.extend(identity for identity in recovered if identity in required or identity in required_groups)
    counts = {status: sum(event["status"] == status for identity, event in latest.items() if identity in entries) for status in FINAL_STATUSES}
    missing = [identity for identity in entries if identity not in latest]
    unfinished = [item for item in manifest.get("attempts_started", []) if not item.get("finished")]
    budget_exceeded = manifest.get("elapsed_wall_seconds", 0) > plan["budget"]["wall_seconds"]
    summary = {"cases": len(entries), "attempts": sum(event.get("record_type") != "group_result" for event in events) + len(unfinished), "quality_passes": counts["passed"],
               "measured": counts["measured"], "completed": counts["completed"],
               "missing": missing, "blockers": sorted(set(blockers)), "recovered": recovered,
               "complete": not missing, "release_allowed": not blockers and not missing and not budget_exceeded,
               "budget_exceeded": budget_exceeded,
               "counts": counts}
    if manifest.get("run_error"):
        summary["release_allowed"] = False
    lines = ["# 测试执行报告", "", f"运行：`{manifest['run_id']}`；计划：`{manifest['plan_id']}`。",
             f"提交：`{manifest['code'].get('commit')}`；环境：`{manifest['environment_id']}`。",
             f"用例 {summary['cases']} 项，尝试 {summary['attempts']} 次，质量通过 {summary['quality_passes']} 项；"
             f"测量完成 {summary['measured']} 项，分类完成 {summary['completed']} 项。",
             "纯测量和无质量映射的分类不计入质量通过。重试通过单列，不等同首次通过。",
             f"批次等级：`{manifest['batch_cost_class']}`；预算：`{_json(manifest['plan']['budget'])}`。",
             f"资源：`{_json(manifest['resources'])}`；实际累计墙钟：{manifest.get('elapsed_wall_seconds', 0):.3f} 秒。",
             f"完成：{summary['complete']}；按本计划放行：{summary['release_allowed']}。", "",
             "| **用例** | **层级/目标/重要性** | **成本/信号** | **状态与判定** | **尝试** | **耗时 ms** | **标签** |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for identity, entry in entries.items():
        event = latest.get(identity, {})
        text = lambda value: str(value).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {text(identity)} | {entry['level']}/{entry['type']}/{entry['priority']} | "
                     f"{entry['cost_class']}/{entry['result_type']} | {event.get('status', '未执行')}/{event.get('verdict')} | "
                     f"{event.get('attempt', 0)} | {event.get('duration_ms', '未知')} | {text(','.join(entry['tags']))} |")
    lines.extend(["", "## 未验证与阻断", "", f"必需项阻断：{', '.join(summary['blockers']) or '无'}。",
                  f"整批墙钟预算超限：{budget_exceeded}。",
                  f"尚无结果：{', '.join(missing) or '无'}。", f"重试后通过：{', '.join(recovered) or '无'}。",
                  f"未选原因：`{_json(manifest.get('excluded', {}))}`。",
                  f"未执行原因：`{_json(manifest.get('unexecuted', {}))}`。"])
    if unfinished:
        lines.append(f"已开始但没有结果的尝试：`{_json(unfinished)}`；保留其进度附件，不能视为通过。")
    if manifest.get("run_error"):
        lines.extend(["", "运行错误：", manifest["run_error"]])
    lines.extend(["", "## 数值、失败及尝试证据", ""])
    for event in events:
        if event.get("failure") or event.get("metrics") or event.get("artifacts"):
            lines.extend([f"`{event['test_id']}`，尝试 {event['attempt']}：", "", "```json",
                          _json({key: event.get(key) for key in ("status", "metrics", "failure", "artifacts", "cost")}), "```", ""])
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def _event(entry, result, manifest, attempt, artifact):
    keys = ("test_id", "level", "type", "priority", "tags", "target", "result_type", "cost_class", "result_detail")
    event = {key: entry[key] for key in keys}
    event.update({key: result.get(key) for key in ("status", "verdict", "category", "metrics", "duration_ms", "started_at", "failure")})
    event.update(run_id=manifest["run_id"], attempt=attempt, plan_id=manifest["plan_id"],
                 environment_id=manifest["environment_id"], artifacts=[artifact],
                 cost={**entry.get("cost_estimate", {}), "actual_wall_time_ms": result["duration_ms"],
                       "actual_peak_memory_bytes": result.get("peak_memory_bytes"),
                       "memory_scope": "own_test_job_committed_bytes" if os.name == "nt" else "unavailable",
                       "memory_unavailable_reason": None if os.name == "nt" else "本平台未取得自身测试进程峰值"})
    return event


def _record_result(directory, entry, result, manifest, attempt, artifact):
    _save_json(directory / artifact, result)
    event = _event(entry, result, manifest, attempt, artifact)
    if entry.get("subtests"):
        event["record_type"] = "group_result"
    seen = {(item["test_id"], item["attempt"]) for item in _events(directory)}
    emitted = []
    if (event["test_id"], attempt) not in seen:
        _append_event(directory, event)
        emitted.append(event)
    if entry.get("subtests"):
        for child in _expanded([entry]):
            observation = next((item for item in result.get("subtests", []) if item["parameters"] == child["parameters"]), None)
            if observation is None:
                observation = {"status": "timeout" if result["status"] == "timeout" else "error",
                               "verdict": None, "category": None, "metrics": [], "duration_ms": None,
                               "started_at": result["started_at"],
                               "failure": [{"phase": "subtest", "type": "MissingResult", "message": "展开参数用例没有取得结果"}]}
            item = _event(child, {**observation, "peak_memory_bytes": None}, manifest, attempt, artifact)
            item["parameters"] = child["parameters"]
            if (item["test_id"], attempt) not in seen:
                _append_event(directory, item)
                emitted.append(item)
    return emitted


def _check_caps(memory):
    if os.name == "nt":
        job = _WindowsJob(memory)
        job.close()
    else:
        raise ValueError("当前计划要求 Windows Job 进程树提交量上限；其它平台尚不能落实同等硬限制")


def run(args, *, root=ROOT):
    invocation_started, invocation_at = time.monotonic(), _now()
    root = Path(root).resolve()
    if args.rebuild_report:
        summary = rebuild_report(args.rebuild_report)
        print(_json(summary))
        return 0 if summary["release_allowed"] else 1
    path = Path(args.manifest) if args.manifest else root / "tests" / "manifest.json"
    catalog = load_catalog(path, root)
    plan, selected, excluded = select_tests(catalog, args)
    if args.list:
        print(_json({"plan_id": args.plan, "selected": selected, "required": plan["required"], "excluded": excluded,
                     "config_sources": catalog.get("config_sources", {})}))
        return 0
    if not selected and not args.resume:
        raise ValueError("筛选未得到任何用例")
    source = _source_info(root, catalog)
    if args.resume:
        directory = Path(args.resume).resolve()
        manifest = _read_json(directory / "manifest.json")
        if manifest["code"]["fingerprint"] != source["fingerprint"]:
            raise ValueError("恢复前代码、清单或环境已变化，必须新建运行")
        if manifest["environment"]["python"] != platform.python_version() or manifest["environment"]["system"] != platform.system():
            raise ValueError("恢复环境与原运行不一致")
        selected, plan = manifest["selected"], manifest["plan"]
        manifest.pop("run_error", None)
        manifest["unexecuted"] = {}
        manifest.setdefault("resumes", []).append({"at": _now(), "allow_resources": args.allow_resource or []})
    else:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:8]
        directory = Path(args.output).resolve() if args.output else root / ".test-results" / run_id
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "artifacts").mkdir()
        manifest = {"schema_version": 1, "run_id": run_id, "plan_id": args.plan,
                    "plan": plan, "batch_cost_class": plan["batch_cost_class"], "matrix": plan.get("matrix", []),
                    "code": source, "command": sys.argv, "selectors": vars(args), "started_at": invocation_at,
                    "environment_id": f"{platform.system()}-python-{platform.python_version()}",
                    "environment": {"system": platform.system(), "release": platform.release(),
                                    "python": platform.python_version(), "executable": sys.executable,
                                    "dependencies": {"unittest": "stdlib"}},
                    "resources": {"allowed": args.allow_resource or [], "parallel": 1,
                                  "memory_control": "WindowsJob commit cap" if os.name == "nt" else "RLIMIT_AS",
                                  "wall_control": "own worker process deadline",
                                  "shared_preparation_cost": "计入整批墙钟，不重复计入逐用例"},
                    "selected": selected, "config_sources": catalog.get("config_sources", {}),
                    "required": [child["test_id"] for entry in catalog["tests"] if entry["test_id"] in plan["required"] for child in _expanded([entry])],
                    "excluded": excluded, "attempts_started": [], "unexecuted": {}, "elapsed_wall_seconds": 0}
    _save_json(directory / "manifest.json", manifest)
    events = _events(directory)
    latest = {event["test_id"]: event for event in events}
    if args.resume:
        by_id = {entry["test_id"]: entry for entry in selected}
        for interrupted in manifest.get("attempts_started", []):
            if interrupted.get("finished") or interrupted["test_id"] not in by_id:
                continue
            if (directory / interrupted["artifact"]).exists():
                result = _read_json(directory / interrupted["artifact"])
            else:
                result = {"status": "error", "verdict": None, "category": None, "metrics": [], "subtests": [],
                          "duration_ms": None, "started_at": interrupted["started_at"],
                          "failure": [{"phase": "interrupted", "type": "InterruptedAttempt", "message": "上次尝试已开始但未保存终态；进度附件仍保留"}]}
            emitted = _record_result(directory, by_id[interrupted["test_id"]], result, manifest,
                                     interrupted["attempt"], interrupted["artifact"])
            events.extend(emitted)
            latest.update({event["test_id"]: event for event in emitted})
            interrupted["finished"] = True
    started = invocation_started
    previous_wall = manifest.get("elapsed_wall_seconds", 0)
    budget = plan["budget"]
    try:
        _check_caps(budget["memory_bytes"])
        for entry in selected:
            identity = entry["test_id"]
            previous = latest.get(identity)
            if args.resume and _acceptable(previous, entry, plan):
                continue
            if args.resume and previous and entry["resources"] and not entry.get("rerun_safe", False):
                manifest["unexecuted"][identity] = "有外部副作用的重跑尚未确认隔离与清理"
                continue
            unavailable = (set(entry["resources"]) | set(plan.get("resources", []))) - set(args.allow_resource or [])
            if unavailable:
                manifest["unexecuted"][identity] = "缺少已允许资源：" + ",".join(sorted(unavailable))
                continue
            stage = next((stage for stage in plan["stages"] if identity in stage["test_ids"]), plan["stages"][-1])
            dependent = [item for item in plan["stages"] if item["plan_id"] in stage["dependencies"]]
            if any(not _acceptable(latest.get(required), {}, plan) for item in dependent for required in item["required"]):
                manifest["unexecuted"][identity] = "计划依赖的必需检查未满足：" + ",".join(stage["dependencies"])
                continue
            if any(not _acceptable(latest.get(dependency), {}, plan) for dependency in entry.get("depends_on", [])):
                manifest["unexecuted"][identity] = "前置测试未满足"
                continue
            remaining = budget["wall_seconds"] - previous_wall - (time.monotonic() - started)
            if remaining <= 0:
                manifest["unexecuted"][identity] = "整批墙钟预算耗尽"
                continue
            attempt = max((event["attempt"] for event in events if event["test_id"] == identity), default=0) + 1
            artifact = "artifacts/" + hashlib.sha256(identity.encode()).hexdigest()[:16] + f"-attempt-{attempt}.json"
            progress = artifact.removesuffix(".json") + "-progress.jsonl"
            started_attempt = {"test_id": identity, "attempt": attempt, "started_at": _now(),
                               "artifact": artifact, "progress": progress, "finished": False}
            manifest.setdefault("attempts_started", []).append(started_attempt)
            _save_json(directory / "manifest.json", manifest)
            result = _execute(entry, root, min(entry["timeout_s"], remaining), min(entry["memory_bytes"], budget["memory_bytes"]), directory / progress)
            for key in ("captured", "worker_stderr"):
                if isinstance(result.get(key), str):
                    result[key] = _redact(result[key], root)
            emitted = _record_result(directory, entry, result, manifest, attempt, artifact)
            events.extend(emitted)
            latest.update({event["test_id"]: event for event in emitted})
            event = latest[identity]
            started_attempt["finished"] = True
            manifest["elapsed_wall_seconds"] = previous_wall + time.monotonic() - started
            _save_json(directory / "manifest.json", manifest)
            rebuild_report(directory)
            stop_reason = None
            if any(failure.get("phase") == "cleanup" for failure in result.get("failure") or []):
                stop_reason = "测试工作单元收尾失败，停止后续调度"
            elif plan.get("stop_on_failure") and not _acceptable(event, entry, plan):
                stop_reason = "计划在前一失败后停止"
            if stop_reason:
                for pending in selected[selected.index(entry) + 1:]:
                    manifest["unexecuted"][pending["test_id"]] = stop_reason
                break
    except BaseException as error:
        manifest["run_error"] = _safe_error(error, root)
    finally:
        manifest["elapsed_wall_seconds"] = previous_wall + time.monotonic() - started
        manifest["finished_at"] = _now()
        _save_json(directory / "manifest.json", manifest)
    summary = rebuild_report(directory)
    print(_json({"directory": str(directory), **summary}))
    return 0 if summary["release_allowed"] else 1


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--manifest")
    value.add_argument("--plan", default="quick")
    value.add_argument("--all", action="store_true")
    value.add_argument("--list", action="store_true")
    for name, allowed in DIMENSIONS.items():
        value.add_argument("--" + name.replace("_", "-"), action="append", choices=sorted(allowed))
    for name in ("tag", "file", "method", "target", "test_id", "resource", "allow_resource"):
        value.add_argument("--" + name.replace("_", "-"), action="append")
    value.add_argument("--max-estimated-ms", type=float)
    value.add_argument("--output")
    value.add_argument("--resume")
    value.add_argument("--rebuild-report")
    return value


def main(argv=None, *, root=ROOT):
    if argv == ["--_worker"]:
        return _worker()
    try:
        return run(parser().parse_args(argv), root=root)
    except (ValueError, OSError, KeyError, TypeError, SyntaxError) as error:
        print(f"测试入口错误：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
