"""CPU、内存及离线对照的纯规则；不取得系统资源或读写文件。"""

from datetime import datetime
import math

from ..contracts import Sample

GIB = 1024 ** 3
BYTE_METRICS = ("available_bytes", "commit_used_bytes", "standby_total_bytes")
RATE_METRICS = ("page_reads_per_sec", "pages_input_per_sec",
                "disk_read_bytes_per_sec", "disk_write_bytes_per_sec")
ALL_METRICS = BYTE_METRICS + RATE_METRICS
AGE_KINDS = ("server_mjs", "cua_repl", "node_repl")
AGE_BUCKETS = ("lt10m", "10to60m", "1to3h", "gt3h", "unknown")
SPIKE_FLOORS = {"page_reads_per_sec": 5.0, "pages_input_per_sec": 50.0,
                "disk_read_bytes_per_sec": 5.0 * 1024 ** 2}


def _number(value, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) and value >= minimum else None
    except OverflowError:
        return None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _record(value):
    if isinstance(value, Sample):
        result = dict(value.metrics)
        result["run_id"] = value.run_id
        result["index"] = value.sequence
        result["started_at_utc"] = value.timestamp
        result["series"] = {**_dict(result.get("series")),
                            "run_id": value.run_id, "index": value.sequence}
        result["processes"] = value.processes or result.get("processes", [])
        result["warnings"] = list(value.errors)
        return result
    if not isinstance(value, dict):
        raise ValueError("样本必须是 Sample 或指标对象")
    return value


def _identity(record):
    series = _dict(record.get("series"))
    run_id = series.get("run_id", record.get("run_id"))
    index = series.get("index", record.get("index"))
    return (run_id if isinstance(run_id, str) and run_id else None,
            index if type(index) is int and index >= 0 else None)


def _stamp(record):
    value = record.get("started_at_utc", record.get("timestamp"))
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def _adjacent(before, after):
    run_a, index_a = _identity(before)
    run_b, index_b = _identity(after)
    if run_a is None or run_a != run_b or index_a is None or index_b != index_a + 1:
        return False
    time_a, time_b = _stamp(before), _stamp(after)
    return time_a is None or time_b is None or time_b > time_a


def metric_values(value):
    record = _record(value)
    physical, commit, cache = (_dict(record.get(key)) for key in ("physical", "commit", "cache"))
    host, rates = _dict(record.get("host")), _dict(record.get("rates"))
    available = _number(physical.get("available_bytes"))
    committed = _number(commit.get("used_bytes"))
    if available is None and "available_bytes" not in physical:
        free = _number(host.get("free_gib"))
        available = _number(free * GIB) if free is not None else None
    if committed is None and "used_bytes" not in commit:
        used = _number(host.get("commit_used_gib"))
        committed = _number(used * GIB) if used is not None else None
    standby = [_number(cache.get(key)) for key in
               ("standby_core_bytes", "standby_normal_bytes", "standby_reserve_bytes")]
    result = {"available_bytes": available, "commit_used_bytes": committed,
              "standby_total_bytes": _number(sum(standby)) if all(v is not None for v in standby) else None}
    result.update({key: _number(rates.get(key)) for key in RATE_METRICS})
    return result


def percentile(sorted_values, fraction):
    if not sorted_values:
        return None
    if _number(fraction) is None or fraction > 1:
        raise ValueError("百分位必须在 0 至 1 之间")
    position = fraction * (len(sorted_values) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    return float(sorted_values[lower] * (upper - position) + sorted_values[upper] * (position - lower)) \
        if lower != upper else float(sorted_values[lower])


def describe(values):
    present = sorted(number for value in values if (number := _number(value)) is not None)
    return {"count": len(present), "missing": len(values) - len(present),
            "min": float(present[0]) if present else None,
            "median": percentile(present, 0.5), "p90": percentile(present, 0.9),
            "p95": percentile(present, 0.95), "max": float(present[-1]) if present else None}


def _integrity(records, expected_count=None):
    issues = []
    identities = [_identity(record) for record in records]
    if not records:
        issues.append("没有样本")
    if any(run is None or index is None for run, index in identities):
        issues.append("缺少运行标识或有效序号")
    if len({run for run, _ in identities}) > 1:
        issues.append("混合运行")
    if len(set(identities)) != len(identities):
        issues.append("重复样本")
    if any(not _adjacent(a, b) for a, b in zip(records, records[1:])):
        issues.append("缺拍、倒序或采样边界改变")
    if expected_count is not None and (type(expected_count) is not int or expected_count < 1
                                       or len(records) != expected_count):
        issues.append("实际样本数与计划不符")
    return {"complete": not issues, "issues": issues}


def detect_transitions(records, threshold_bytes=GIB):
    if _number(threshold_bytes) is None or threshold_bytes <= 0:
        raise ValueError("跃迁阈值必须为有限正数")
    records = [_record(record) for record in records]
    values = [metric_values(record) for record in records]
    transitions = []
    for index in range(1, len(records)):
        if not _adjacent(records[index - 1], records[index]):
            continue
        for metric in BYTE_METRICS[:2]:
            before, after = values[index - 1][metric], values[index][metric]
            if before is None or after is None:
                continue
            delta = after - before
            if abs(delta) >= threshold_bytes:
                transitions.append({"index": index, "metric": metric,
                                    "started_at_utc": records[index].get("started_at_utc"),
                                    "from_bytes": before, "to_bytes": after, "delta_bytes": delta})
    return transitions


def _processes(record):
    value = record.get("processes")
    return _list(value.get("top")) if isinstance(value, dict) else _list(value)


def summarize_process_owners(records):
    ranked, owners = 0, {}
    for record in map(_record, records):
        processes = _processes(record)
        if not processes:
            continue
        ranked += 1
        largest, counts = {}, {}
        for process in processes:
            process = _dict(process)
            name, size = process.get("name"), _number(process.get("working_set_bytes"))
            if not isinstance(name, str) or size is None:
                continue
            counts[name] = counts.get(name, 0) + 1
            largest[name] = max(largest.get(name, 0), size)
        for name, size in largest.items():
            entry = owners.setdefault(name, {"sizes": [], "max_instances_in_top": 0})
            entry["sizes"].append(size)
            entry["max_instances_in_top"] = max(entry["max_instances_in_top"], counts[name])
    result = [{"name": name, "samples_present": len(entry["sizes"]),
               "max_instances_in_top": entry["max_instances_in_top"],
               "largest_instance_median_bytes": describe(entry["sizes"])["median"],
               "largest_instance_max_bytes": max(entry["sizes"])} for name, entry in owners.items()]
    result.sort(key=lambda item: (-item["largest_instance_median_bytes"], item["name"]))
    return {"ranked_samples": ranked, "owners": result,
            "basis": "largest single instance per sample; same-name instances not summed; "
                     "working set includes shared pages and is not a reclaimable amount"}


def summarize_records(records, transition_threshold_bytes=GIB):
    records = [_record(record) for record in records]
    values = [metric_values(record) for record in records]
    warnings = [warning for record in records for warning in _list(record.get("warnings"))
                if isinstance(warning, str)]
    starts = [record.get("started_at_utc") for record in records if record.get("started_at_utc")]
    return {"mode": "read-only-analysis", "cleanup_performed": False,
            "sample_count": len(records), "run_ids": sorted({run for run, _ in map(_identity, records) if run}),
            "started_at_utc": starts[0] if starts else None,
            "finished_at_utc": starts[-1] if starts else None,
            "warning_count": len(warnings),
            "overrun_count": sum("overrun" in warning.lower() for warning in warnings),
            "first_tick_rate_warning_count": sum("no previous PDH interval" in warning for warning in warnings),
            "metrics": {key: describe([row[key] for row in values]) for key in ALL_METRICS},
            "transition_threshold_bytes": transition_threshold_bytes,
            "transitions": detect_transitions(records, transition_threshold_bytes),
            "integrity": _integrity(records), "process_owners": summarize_process_owners(records)}


def _process_identity(process):
    pid, created = process.get("pid"), process.get("created_at")
    if type(pid) is not int or pid < 0 or not isinstance(created, str) or not created:
        return None
    return pid, created


def cpu_intervals(records):
    records = [_record(record) for record in records]
    result = []
    for index, current in enumerate(records):
        cpu = _dict(current.get("cpu"))
        item = {"sequence": _identity(current)[1], "scope": cpu.get("scope", "unknown"),
                "coverage": cpu.get("coverage", "unknown"), "busy_pct": None,
                "system_busy_pct": None, "group_busy_pct": None, "processes": [], "missing_reason": None}
        if index == 0:
            item["missing_reason"] = "首拍没有前一累计值"
            result.append(item)
            continue
        previous = records[index - 1]
        old = _dict(previous.get("cpu"))
        if not _adjacent(previous, current):
            item["missing_reason"] = "样本不连续或运行改变"
            result.append(item)
            continue
        stable_scope = (cpu.get("scope") in ("system", "group")
                        and cpu.get("coverage") == old.get("coverage") == "complete"
                        and cpu.get("scope") == old.get("scope")
                        and cpu.get("processor_group") == old.get("processor_group")
                        and cpu.get("logical_processors") == old.get("logical_processors")
                        and type(cpu.get("logical_processors")) is int
                        and cpu["logical_processors"] > 0)
        totals = [_number(value) for value in (cpu.get("total_seconds"), old.get("total_seconds"),
                                             cpu.get("idle_seconds"), old.get("idle_seconds"))]
        if stable_scope and all(value is not None for value in totals):
            total_delta, idle_delta = totals[0] - totals[1], totals[2] - totals[3]
            if total_delta > 0 and 0 <= idle_delta <= total_delta:
                item["busy_pct"] = (total_delta - idle_delta) / total_delta * 100
                item["system_busy_pct" if cpu["scope"] == "system" else "group_busy_pct"] = item["busy_pct"]
        if item["busy_pct"] is None:
            item["missing_reason"] = "累计值、间隔或处理器覆盖不足"
        before_time, after_time = _stamp(previous), _stamp(current)
        elapsed = (after_time - before_time).total_seconds() if before_time and after_time else None
        before_processes = {}
        duplicate_ids = set()
        for process in _processes(previous):
            process = _dict(process)
            identity = _process_identity(process)
            if identity in before_processes:
                duplicate_ids.add(identity)
            if identity is not None:
                before_processes[identity] = process
        current_processes = [_dict(process) for process in _processes(current)]
        current_ids = set()
        for process in current_processes:
            identity = _process_identity(process)
            if identity in current_ids:
                duplicate_ids.add(identity)
            current_ids.add(identity)
        for process in current_processes:
            identity = _process_identity(process)
            old_process = before_processes.get(identity, {}) if identity not in duplicate_ids else {}
            now, before = _number(process.get("cpu_seconds")), _number(old_process.get("cpu_seconds"))
            single = None
            if elapsed and elapsed > 0 and identity is not None and now is not None and before is not None and now >= before:
                single = _number((now - before) / elapsed * 100)
            normalized = (single / cpu["logical_processors"] if single is not None and stable_scope
                          and cpu["scope"] == "system" else None)
            item["processes"].append({"pid": process.get("pid"), "created_at": process.get("created_at"),
                                      "name": process.get("name"), "single_core_pct": single,
                                      "system_normalized_pct": normalized})
        result.append(item)
    return result


def sample_view(value):
    record = _record(value)
    host, commit = _dict(record.get("host")), _dict(record.get("commit"))
    values = metric_values(record)
    used, available = values["commit_used_bytes"], values["available_bytes"]
    limit = _number(commit.get("limit_bytes"))
    if "limit_bytes" not in commit:
        legacy_limit = _number(host.get("commit_limit_gib"))
        limit = _number(legacy_limit * GIB) if legacy_limit is not None else None
    owners = {}
    for item in _list(record.get("top_owners")):
        item = _dict(item)
        private = _number(item.get("private_gib"))
        if isinstance(item.get("owner"), str) and private is not None:
            owners[item["owner"]] = private
    return {"local": record.get("local", record.get("started_at_utc")),
            "free_gib": available / GIB if available is not None else None,
            "commit_pct": used / limit * 100 if used is not None and limit and used <= limit else None,
            "owners": owners}


def owner_growth(before, after, top=5):
    if type(top) is not int or top < 0:
        raise ValueError("增长排名数量必须是非负整数")
    grown = []
    for owner, value in after["owners"].items():
        if owner in before["owners"] and value > before["owners"][owner]:
            grown.append({"owner": owner, "private_gib": round(value, 3),
                          "delta_gib": round(value - before["owners"][owner], 3)})
    grown.sort(key=lambda item: (-item["delta_gib"], item["owner"]))
    return {"grown": grown[:top],
            "entered_top_list": sorted(set(after["owners"]) - set(before["owners"]))}


def _thresholds(free_below_gib, commit_above_pct, sustain):
    if _number(free_below_gib) is None or _number(commit_above_pct) is None or commit_above_pct > 100:
        raise ValueError("压力阈值必须是有效有限数值")
    if type(sustain) is not int or sustain < 1:
        raise ValueError("持续拍数必须是正整数")
    return {"free_below_gib": free_below_gib, "commit_above_pct": commit_above_pct,
            "sustain_samples": sustain, "status": "proposal"}


def _breaches(view, thresholds):
    reasons = []
    if view["free_gib"] is not None and view["free_gib"] < thresholds["free_below_gib"]:
        reasons.append("free_below")
    if view["commit_pct"] is not None and view["commit_pct"] > thresholds["commit_above_pct"]:
        reasons.append("commit_above")
    return reasons


def _runs(records, *, tree_only=False):
    runs = []
    for record in map(_record, records):
        if tree_only and not any(key in record for key in ("host", "codex_tree", "commit_residual")):
            continue
        if record.get("kind") not in (None, "tree-series", "sample"):
            continue
        if not runs or _identity(record)[0] != _identity(runs[-1][-1])[0]:
            runs.append([])
        runs[-1].append(record)
    return runs


def evaluate(records, free_below_gib=4.0, commit_above_pct=85.0, sustain=3):
    thresholds = _thresholds(free_below_gib, commit_above_pct, sustain)
    results = []
    for run in _runs(records):
        views = [sample_view(record) for record in run]
        reasons = [_breaches(view, thresholds) for view in views]
        episodes, start, longest = [], None, 0
        for index in range(len(run) + 1):
            continuing = index < len(run) and bool(reasons[index])
            if start is not None and index < len(run) and not _adjacent(run[index - 1], run[index]):
                continuing = False
            if start is not None and not continuing:
                end = index - 1
                longest = max(longest, end - start + 1)
                if end - start + 1 >= sustain:
                    baseline = start - 1 if start > 0 and _adjacent(run[start - 1], run[start]) else start
                    frees = [v["free_gib"] for v in views[start:index] if v["free_gib"] is not None]
                    pcts = [v["commit_pct"] for v in views[start:index] if v["commit_pct"] is not None]
                    episodes.append({"start_local": views[start]["local"], "end_local": views[end]["local"],
                                     "samples": end - start + 1,
                                     "reasons": sorted({reason for row in reasons[start:index] for reason in row}),
                                     "min_free_gib": min(frees) if frees else None,
                                     "max_commit_pct": max(pcts) if pcts else None,
                                     "growth_baseline": "previous sample" if baseline != start else "first breaching sample",
                                     "owner_growth": owner_growth(views[baseline], views[end])})
                start = None
            if index < len(run) and reasons[index] and start is None:
                start = index
        frees = [v["free_gib"] for v in views if v["free_gib"] is not None]
        pcts = [v["commit_pct"] for v in views if v["commit_pct"] is not None]
        results.append({"run_id": _identity(run[0])[0], "interval_s": _number(run[0].get("interval_s")),
                        "samples": len(run), "first_local": views[0]["local"], "last_local": views[-1]["local"],
                        "breaching_samples": sum(bool(row) for row in reasons), "longest_breach_samples": longest,
                        "min_free_gib": min(frees) if frees else None, "max_commit_pct": max(pcts) if pcts else None,
                        "episodes": episodes, "integrity": _integrity(run)})
    return {"mode": "read-only", "actions_taken": False, "thresholds": thresholds, "runs": results,
            "basis": "thresholds are proposals; missing data is unknown; private commit is not reclaimable memory"}


def _stats(values, *, signed=False):
    values = [number for value in values
              if (number := _number(value, minimum=-math.inf if signed else 0)) is not None]
    return {"samples": len(values), "first": values[0] if values else None,
            "last": values[-1] if values else None, "min": min(values) if values else None,
            "max": max(values) if values else None,
            "mean": sum(value / len(values) for value in values) if values else None}


def _pearson(pairs):
    pairs = [(x, y) for x, y in pairs if _number(x) is not None and _number(y, minimum=-math.inf) is not None]
    if len(pairs) < 3:
        return None
    scale_x = max(abs(x) for x, _ in pairs)
    scale_y = max(abs(y) for _, y in pairs)
    if not scale_x or not scale_y:
        return None
    xs, ys = [x / scale_x for x, _ in pairs], [y / scale_y for _, y in pairs]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    xs, ys = [x - mx for x in xs], [y - my for y in ys]
    if not any(xs) or not any(ys):
        return None
    return round(sum(x * y for x, y in zip(xs, ys)) /
                 math.sqrt(sum(x * x for x in xs) * sum(y * y for y in ys)), 3)


def _age_kind(records, kind):
    entries = [next((item for item in _list(_dict(record.get("codex_tree")).get("age_buckets"))
                     if isinstance(item, dict) and item.get("kind") == kind), {}) for record in records]
    return {"samples_with_field": sum(bool(entry) for entry in entries),
            "oldest_minutes": _stats([entry.get("oldest_minutes") for entry in entries]),
            "buckets": {bucket: {field: _stats([_dict(entry.get(bucket)).get(field) for entry in entries])
                                  for field in ("processes", "private_gib")} for bucket in AGE_BUCKETS}}


def _event_total(records, field):
    if not records:
        return None
    values = [_number(record.get(field)) for record in records]
    return _number(sum(values)) if all(value is not None for value in values) else None


def summarize(records):
    results = []
    for run in _runs(records, tree_only=True):
        tree = [_dict(record.get("codex_tree")) for record in run]
        host = [_dict(record.get("host")) for record in run]
        residual = [_dict(record.get("commit_residual")) for record in run]
        processes = [_number(record.get("processes")) for record in run]
        residuals = [_number(row.get("residual_gib"), minimum=-math.inf) for row in residual]
        result = {"run_id": _identity(run[0])[0], "samples": len(run),
                  "interval_s": _number(run[0].get("interval_s")), "declared_count": _number(run[0].get("count")),
                  "first_local": run[0].get("local"), "last_local": run[-1].get("local"),
                  "host_free_gib": _stats([row.get("free_gib") for row in host]),
                  "host_commit_used_gib": _stats([row.get("commit_used_gib") for row in host]),
                  "processes": _stats(processes), "born_total": _event_total(run[1:], "born_since_prev"),
                  "exited_total": _event_total(run[1:], "exited_since_prev"),
                  "age": {kind: _age_kind(run, kind) for kind in AGE_KINDS}, "integrity": _integrity(run),
                  "codex_tree": {field: _stats([row.get(field) for row in tree])
                                 for field in ("processes", "private_gib", "server_mjs", "server_mjs_private_gib")},
                  "commit_residual": {"residual_gib": _stats(residuals, signed=True),
                                      "residual_pct_of_commit": _stats([row.get("residual_pct_of_commit") for row in residual], signed=True),
                                      "pearson_residual_vs_processes": _pearson(list(zip(processes, residuals)))}}
        result["codex_tree"].update(born_total=_event_total(tree[1:], "born_since_prev"),
                                    exited_total=_event_total(tree[1:], "exited_since_prev"))
        results.append(result)
    return {"mode": "read-only", "actions_taken": False, "runs": results,
            "basis": "per-sample aggregates; private commit and residuals are not reclaimable memory"}


def analyze(samples, *, conditions=None):
    conditions = {} if conditions is None else conditions
    if not isinstance(conditions, dict):
        raise ValueError("分析条件必须是对象")
    top = conditions.get("top", 10)
    if type(top) is not int or not 1 <= top <= 100:
        raise ValueError("top 必须是 1..100 的整数")
    records = [_record(sample) for sample in samples]
    result = summarize_records(records, conditions.get("transition_threshold_bytes", GIB))
    result["cpu"] = {"intervals": cpu_intervals(records), "action": "diagnosis-only"}
    result["pressure"] = evaluate(records, conditions.get("free_below_gib", 4.0),
                                  conditions.get("commit_above_pct", 85.0), conditions.get("sustain", 3))
    result["tree_summary"] = summarize(records)
    processes = _processes(records[-1]) if records else []
    ranked = [process for process in processes if isinstance(process, dict)
              and _number(process.get("working_set_bytes")) is not None]
    ranked.sort(key=lambda process: process["working_set_bytes"], reverse=True)
    result["process_ranking"] = {"limit": top, "ranked": ranked[:top], "total_readable": len(processes),
                                 "basis": "working_set_including_shared_pages",
                                 "coverage": records[-1].get("process_coverage", {"coverage": "unknown"}) if records else {"coverage": "unknown"}}
    result["caveats"] = ["Standby 已计入可用内存，Free 增长不等于收益。",
                         "工作集含共享页，排名、私有提交和残差均不是可回收量。",
                         "CPU 高低不能证明可结束进程；本规则不执行任何系统动作。"]
    return result


def _window(records, metadata):
    rows = [metric_values(record) for record in records]
    warmup = _warmup(metadata, len(records))
    rate_rows = rows[warmup:] if warmup is not None else []
    metrics = {}
    for key in ALL_METRICS:
        values = [row[key] for row in (rate_rows if key in RATE_METRICS else rows)]
        stats = describe(values)
        present = [value for value in values if value is not None]
        stats.update(mean=sum(value / len(present) for value in present) if present else None,
                     first=values[0] if values else None,
                     tail_median=describe(values[len(values) * 2 // 3:])["median"])
        metrics[key] = stats
    return {"samples": len(records), "run_ids": sorted({run for run, _ in map(_identity, records) if run}),
            "started_at_utc": records[0].get("started_at_utc") if records else None,
            "finished_at_utc": records[-1].get("started_at_utc") if records else None,
            "metrics": metrics, "integrity": _integrity(records)}


def _header(metadata):
    metadata = _dict(metadata)
    return _dict(metadata.get("header", metadata))


def _warmup(metadata, sample_count):
    value = _header(metadata).get("warmup_samples", 1)
    return value if type(value) is int and value in (0, 1) and value < sample_count else None


def _window_evidence(records, metadata, name):
    header, terminal = _header(metadata), _dict(_dict(metadata).get("terminal"))
    issues = [f"{name}: {issue}" for issue in _integrity(records, header.get("expected_count")).get("issues", [])]
    for key in ("schema_version", "program_version", "rule_version", "build_id", "environment_id", "workload_id", "experiment_id"):
        value = header.get(key)
        if value is None or isinstance(value, bool) or value == "":
            issues.append(f"{name}: 缺少 {key}")
    count = header.get("expected_count")
    if type(count) is not int or count < 3 or count != len(records):
        issues.append(f"{name}: 缺少完整的计划拍数")
    interval = _number(header.get("interval_s"))
    if interval is None or interval < 1:
        issues.append(f"{name}: 缺少有效采样间隔")
    if terminal.get("completion") != "success" or terminal.get("sample_count") != len(records):
        issues.append(f"{name}: 缺少成功终态或实际拍数")
    if header.get("pre_registered") is not True:
        issues.append(f"{name}: 缺少事前固定的实验条件")
    if records and [_identity(record)[1] for record in records] != list(range(len(records))):
        issues.append(f"{name}: 窗口未从第零拍完整保存")
    warmup = _warmup(metadata, len(records))
    if warmup is None:
        issues.append(f"{name}: 速率预热条件无效")
    for index, record in enumerate(records):
        if _stamp(record) is None:
            issues.append(f"{name}: 样本时间无效")
            break
        values = metric_values(record)
        if any(values[key] is None for key in BYTE_METRICS):
            issues.append(f"{name}: 容量指标缺失")
            break
        if warmup is not None and index >= warmup and any(values[key] is None for key in RATE_METRICS):
            issues.append(f"{name}: 必要速率缺失")
            break
    return issues


def _diff(after, before):
    return None if before is None or after is None else after - before


def compare(samples_a, samples_b, *, conditions=None, metadata_a=None, metadata_b=None, null_pairs=None):
    conditions = {} if conditions is None else conditions
    if not isinstance(conditions, dict):
        raise ValueError("比较条件必须是对象")
    gain_gib, spike_factor = conditions.get("gain_gib", 0.5), conditions.get("spike_factor", 1.5)
    if (_number(gain_gib) is None or gain_gib <= 0 or _number(gain_gib * GIB) is None
            or _number(spike_factor) is None or spike_factor <= 1):
        raise ValueError("比较阈值必须是有限正值，spike_factor 必须大于 1")
    records_a, records_b = list(map(_record, samples_a)), list(map(_record, samples_b))
    a, b = _window(records_a, metadata_a), _window(records_b, metadata_b)
    am, bm = a["metrics"], b["metrics"]
    deltas = {"available_tail_median_bytes": _diff(bm["available_bytes"]["tail_median"], am["available_bytes"]["tail_median"]),
              "commit_median_bytes": _diff(bm["commit_used_bytes"]["median"], am["commit_used_bytes"]["median"]),
              "standby_median_bytes": _diff(bm["standby_total_bytes"]["median"], am["standby_total_bytes"]["median"])}
    issues = _window_evidence(records_a, metadata_a, "A") + _window_evidence(records_b, metadata_b, "B")
    ha, hb = _header(metadata_a), _header(metadata_b)
    for key in ("schema_version", "program_version", "rule_version", "build_id", "environment_id", "workload_id", "experiment_id", "interval_s", "expected_count", "conditions"):
        if ha.get(key) != hb.get(key) or (key == "conditions" and ha.get(key) != conditions):
            issues.append(f"A/B: {key} 不一致")
    if ha.get("warmup_samples", 1) != hb.get("warmup_samples", 1):
        issues.append("A/B: 速率预热条件不一致")
    if a["run_ids"] == b["run_ids"]:
        issues.append("A/B: 必须是不同运行的独立窗口")
    action_a, action_b = _dict(_dict(metadata_a).get("action")), _dict(_dict(metadata_b).get("action"))
    if action_a.get("attempted") is not False or action_a.get("call_outcome") != "not_started":
        issues.append("A: 未确认无维护的对照")
    if (action_b.get("attempted") is not True or action_b.get("call_outcome") != "success"
            or action_b.get("privilege_restored") is not True or action_b.get("recording_complete") is not True
            or hb.get("mechanism") not in ("working-set", "standby")):
        issues.append("B: 维护、恢复或记录事实不完整")
    null_ceiling, valid_pairs, raw_flags = {}, 0, 0
    null_level_noise = {"available_bytes": 0, "commit_used_bytes": 0}
    null_cpu_noise = 0
    if not null_pairs:
        issues.append("缺少 A/A 校准")
    for pair in null_pairs or ():
        if not isinstance(pair, dict):
            issues.append("A/A: 缺少窗口元数据")
            continue
        na, nb = list(map(_record, pair.get("a", []))), list(map(_record, pair.get("b", [])))
        ma, mb = pair.get("metadata_a"), pair.get("metadata_b")
        problems = _window_evidence(na, ma, "A/A-A") + _window_evidence(nb, mb, "A/A-B")
        if {_identity(row)[0] for row in na} & {_identity(row)[0] for row in nb}:
            problems.append("A/A: 必须使用独立运行")
        for meta in (ma, mb):
            nh, action = _header(meta), _dict(_dict(meta).get("action"))
            if action.get("attempted") is not False or action.get("call_outcome") != "not_started":
                problems.append("A/A: 未确认零维护")
            if (any(nh.get(key) != ha.get(key) for key in ("schema_version", "program_version", "rule_version", "build_id", "environment_id", "workload_id", "interval_s", "expected_count", "conditions"))
                    or nh.get("warmup_samples", 1) != ha.get("warmup_samples", 1)):
                problems.append("A/A: 校准条件与实验不一致")
        if problems:
            issues.extend(problems)
            continue
        valid_pairs += 1
        nma, nmb = _window(na, ma)["metrics"], _window(nb, mb)["metrics"]
        for key in null_level_noise:
            statistic = "tail_median" if key == "available_bytes" else "median"
            null_level_noise[key] = max(null_level_noise[key],
                                        abs(nmb[key][statistic] - nma[key][statistic]))
        null_cpu_a = [row["system_busy_pct"] for row in cpu_intervals(na)[1:]]
        null_cpu_b = [row["system_busy_pct"] for row in cpu_intervals(nb)[1:]]
        if not null_cpu_a or not null_cpu_b or None in null_cpu_a + null_cpu_b:
            issues.append("A/A: CPU 校准覆盖不足")
        else:
            null_cpu_noise = max(null_cpu_noise, abs(sum(null_cpu_b) / len(null_cpu_b)
                                                    - sum(null_cpu_a) / len(null_cpu_a)))
        flagged = False
        for key, floor in SPIKE_FLOORS.items():
            if nma[key]["mean"] is None or nmb[key]["mean"] is None:
                issues.append("A/A: 校准速率缺失")
                continue
            limit = _number(nma[key]["mean"] * spike_factor + floor)
            if limit is None:
                issues.append("A/A: 速率阈值溢出")
                continue
            ratio = nmb[key]["mean"] / limit
            null_ceiling[key] = max(null_ceiling.get(key, 0), ratio)
            flagged = flagged or ratio > 1
        raw_flags += flagged
    refault = {}
    for key, floor in SPIKE_FLOORS.items():
        mean_a, mean_b = am[key]["mean"], bm[key]["mean"]
        if mean_a is None or mean_b is None:
            refault[key] = None
            continue
        limit = _number(mean_a * spike_factor + floor)
        if limit is None:
            issues.append("A/B: 速率阈值溢出")
            refault[key] = None
            continue
        ratio = mean_b / limit
        refault[key] = {"a_mean": mean_a, "b_mean": mean_b, "limit": limit,
                        "spike": ratio > 1, "excess_ratio": ratio,
                        "null_max_excess_ratio": null_ceiling.get(key),
                        "beyond_null": ratio > null_ceiling[key] if key in null_ceiling else None}
    write_a, write_b = am["disk_write_bytes_per_sec"]["mean"], bm["disk_write_bytes_per_sec"]["mean"]
    if write_a is not None and write_b is not None and write_b > write_a:
        issues.append("磁盘写入增加，缺少写入代价判定阈值")
    application_a = _dict(_dict(metadata_a).get("application"))
    application_b = _dict(_dict(metadata_b).get("application"))
    values = [_number(application.get("value")) for application in (application_a, application_b)]
    if (any(value is None for value in values) or application_a.get("direction") not in ("higher", "lower")
            or any(not application_a.get(key) or application_a.get(key) != application_b.get(key)
                   for key in ("metric", "unit", "direction"))):
        issues.append("缺少可比较的用户任务吞吐或响应指标")
    cpu_a, cpu_b = cpu_intervals(records_a), cpu_intervals(records_b)
    if any(row["system_busy_pct"] is None for rows in (cpu_a, cpu_b) for row in rows[1:]) or min(len(cpu_a), len(cpu_b)) < 2:
        issues.append("CPU 时间或全机覆盖不足")
    if not valid_pairs or any(key not in null_ceiling for key in SPIKE_FLOORS):
        issues.append("A/A 校准不完整")
    if _dict(_dict(metadata_b).get("terminal")).get("recovery_complete") is not True:
        issues.append("B: 缺少完成恢复窗口的证据")
    issues = list(dict.fromkeys(issues))
    verdict, reason = "insufficient_evidence", "；".join(issues)
    if not issues:
        app_worse = values[1] < values[0] if application_a["direction"] == "higher" else values[1] > values[0]
        costly = any(spike and spike["spike"] and spike["beyond_null"] for spike in refault.values())
        cpu_delta = (sum(row["system_busy_pct"] for row in cpu_b[1:]) / (len(cpu_b) - 1)
                     - sum(row["system_busy_pct"] for row in cpu_a[1:]) / (len(cpu_a) - 1))
        material = ((deltas["available_tail_median_bytes"] or 0) >= gain_gib * GIB
                    and deltas["available_tail_median_bytes"] > null_level_noise["available_bytes"]
                    or (deltas["commit_median_bytes"] is not None
                        and -deltas["commit_median_bytes"] >= gain_gib * GIB
                        and -deltas["commit_median_bytes"] > null_level_noise["commit_used_bytes"]))
        if costly or app_worse or cpu_delta > null_cpu_noise:
            verdict, reason = "cosmetic-or-harmful", "重调页代价超过校准或用户任务表现下降"
        elif material:
            verdict, reason = "benefit-for-this-workload", "本次负载资源改善持续，已校准速率及用户任务指标未显示退化"
        else:
            verdict, reason = "cosmetic", "没有实质提交下降或持续可用量改善"
    return {"mode": "offline-ab-compare", "cleanup_performed": False,
            "A": a, "B": b, "deltas": deltas, "refault": refault,
            "null_calibration": {"pairs": valid_pairs, "max_excess_ratio": null_ceiling,
                                 "level_noise_bytes": null_level_noise, "cpu_noise_pct": null_cpu_noise,
                                 "pairs_flagged_by_raw_rule": raw_flags} if null_pairs else None,
            "thresholds": {"gain_gib": gain_gib, "spike_factor": spike_factor,
                           "spike_floors": dict(SPIKE_FLOORS), "tail_fraction": "last third of each window"},
            "verdict": verdict, "reason": reason, "evidence_issues": issues,
            "caveats": ["结论仅适用于已记录的机制、负载与环境；前后容量变化不能单独证明加速。"]}
