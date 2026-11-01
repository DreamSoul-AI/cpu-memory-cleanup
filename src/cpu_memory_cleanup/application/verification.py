"""计时与工作集的证据核验。只消费记录，不采样、不派发维护。"""

from datetime import datetime
from ..contracts import ProcessTarget, finite_number


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp if stamp.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _number(value):
    try:
        return finite_number(value, "metric")
    except ValueError:
        return None


def _rows(snapshot):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("processes"), list):
        raise ValueError("工作集观察缺少进程列表")
    errors = snapshot.get("errors")
    if not isinstance(errors, list) or any(not isinstance(error, str) for error in errors):
        raise ValueError("工作集观察错误字段无效")
    result = {}
    for row in snapshot["processes"]:
        if not isinstance(row, dict):
            raise ValueError("工作集观察项无效")
        target = ProcessTarget(row.get("pid"), row.get("created_at"), row.get("name", ""), row.get("image_path", ""))
        key = (target.pid, target.created_at)
        if key in result:
            raise ValueError("工作集观察身份重复")
        result[key] = (row, finite_number(row.get("working_set_bytes"), "working_set_bytes"))
    return result


def working_set_changes(before=None, after=None):
    result = {"coverage": "not_observed", "items": [], "before_readable": None,
              "after_readable": None, "matched": 0, "appeared": [], "disappeared": [],
              "errors": [], "all_eligible_pages_trimmed": None,
              "system_released_bytes": None}
    if before is None or after is None:
        result["errors"].append("缺少前后逐进程工作集观察，不能判断处理覆盖")
        return result
    a, b = _rows(before), _rows(after)
    result.update(before_readable=len(a), after_readable=len(b),
                  coverage="partial" if before["errors"] or after["errors"] else "observed_only",
                  errors=before["errors"] + after["errors"])
    for key in sorted(a.keys() & b.keys()):
        old, x = a[key]
        new, y = b[key]
        status = "decreased" if y < x else "increased" if y > x else "unchanged"
        result["items"].append({"pid": key[0], "created_at": key[1], "name": old.get("name", ""),
                                "before_bytes": x, "after_bytes": y, "delta_bytes": y - x,
                                "status": status})
    result["matched"] = len(result["items"])
    result["appeared"] = [{"pid": k[0], "created_at": k[1]} for k in sorted(b.keys() - a.keys())]
    result["disappeared"] = [{"pid": k[0], "created_at": k[1]} for k in sorted(a.keys() - b.keys())]
    return result


def memory_timeline(samples, header, *, threshold_bytes=None):
    if threshold_bytes is not None:
        finite_number(threshold_bytes, "threshold_bytes", minimum=1)
    rows = []
    for sample in samples:
        physical = sample.metrics.get("physical") or {}
        rows.append({"timestamp": sample.timestamp, "available_bytes": _number(physical.get("available_bytes"))})
    valid = [r for r in rows if r["available_bytes"] is not None]
    initial = rows[0]["available_bytes"] if rows else None
    last = rows[-1]["available_bytes"] if len(rows) > 1 else None
    peak = max(valid, key=lambda r: r["available_bytes"]) if valid else None
    gain = next((r for r in rows[1:] if initial is not None and r["available_bytes"] is not None
                 and r["available_bytes"] > initial), None)
    significant = next((r for r in rows[1:] if initial is not None and r["available_bytes"] is not None
                        and threshold_bytes is not None and r["available_bytes"] - initial >= threshold_bytes), None)
    stamps = [_stamp(row["timestamp"]) for row in rows]
    intervals = [(b - a).total_seconds() * 1000 for a, b in zip(stamps, stamps[1:]) if a is not None and b is not None]
    ordered = bool(rows) and all(stamp is not None for stamp in stamps) and len(intervals) == len(rows) - 1 and all(x >= 0 for x in intervals)
    start = _stamp(header.get("started_at"))
    first = _stamp(gain["timestamp"]) if gain else None
    delay = (first - start).total_seconds() * 1000 if first is not None and start is not None and first >= start and ordered else None
    return {"window": "post_observation" if header.get("post_observation_seconds") else "immediate_snapshots",
            "request_started_at": header.get("started_at"), "request_time_source": "package_header",
            "window_started_at": rows[0]["timestamp"] if rows else None,
            "window_ended_at": rows[-1]["timestamp"] if rows else None,
            "samples": rows, "available_delta_bytes": None if initial is None or last is None else last - initial,
            "peak_observed_available_bytes": peak["available_bytes"] if peak else None,
            "peak_observed_at": peak["timestamp"] if peak else None,
            "rebound_used_bytes": None if peak is None or last is None else peak["available_bytes"] - last,
            "first_observed_gain_at": gain["timestamp"] if gain else None,
            "first_observed_gain_delay_ms": delay,
            "significance_threshold_bytes": threshold_bytes,
            "first_significant_gain_at": significant["timestamp"] if significant and ordered else None,
            "sample_interval_ms": {"min": min(intervals), "max": max(intervals)} if intervals and ordered else None,
            "time_order_valid": ordered, "exact_drop_time_known": False}


def validate_attachment(value, run_id):
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1 or value.get("run_id") != run_id:
        raise ValueError("诊断附件版本或运行身份无效")
    timing = value.get("timing")
    if timing is not None:
        if not isinstance(timing, dict) or timing.get("basis") != "run_monotonic" or timing.get("unit") != "ms":
            raise ValueError("诊断计时单位或基准无效")
        for key in ("stages", "events", "errors"):
            if not isinstance(timing.get(key), list):
                raise ValueError("诊断计时字段无效")
        if timing.get("elapsed_ms") is not None:
            finite_number(timing["elapsed_ms"], "elapsed_ms")
        for row in timing["stages"]:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                raise ValueError("诊断阶段无效")
            for key in ("start_ms", "end_ms", "duration_ms"):
                if row.get(key) is not None:
                    finite_number(row[key], key)
            if row.get("start_ms") is not None and row.get("end_ms") is not None and row["end_ms"] < row["start_ms"]:
                raise ValueError("诊断阶段时间倒置")
            if all(row.get(key) is not None for key in ("start_ms", "end_ms", "duration_ms")) and abs(row["end_ms"] - row["start_ms"] - row["duration_ms"]) > 0.000001:
                raise ValueError("诊断阶段持续时间不一致")
        for event in timing["events"]:
            if not isinstance(event, dict) or not isinstance(event.get("name"), str):
                raise ValueError("诊断事件无效")
            if event.get("offset_ms") is not None:
                finite_number(event["offset_ms"], "offset_ms")
    observations = value.get("working_sets")
    if observations is not None:
        if not isinstance(observations, dict):
            raise ValueError("工作集附件无效")
        for snapshot in observations.values():
            if snapshot is not None:
                _rows(snapshot)
    return value


def diagnostics(samples, header, *, timing=None, working_sets=None, source="live", threshold_bytes=None):
    observations = working_sets or {}
    scope = header.get("scope", "system")
    # 定向原记录已经在同一目标会话中取得前后值，不追加查询。
    if scope == "process" and len(samples) >= 2 and not observations:
        target = header.get("target") or {}
        observations = {name: {"processes": [{**target, **(sample.metrics.get("target") or {})}], "errors": sample.errors}
                        for name, sample in (("before", samples[0]), ("after", samples[-1]))}
    native = [row.get("duration_ms") for row in (timing or {}).get("stages", []) if row.get("name") == "native_call"]
    return {"version": 1, "run_id": header.get("run_id"), "source": source, "timing": timing,
            "working_sets": observations or None,
            "verification": {"scope": scope, "working_sets": working_set_changes(observations.get("before"), observations.get("after")),
                             "timeline": memory_timeline(samples, header, threshold_bytes=threshold_bytes),
                             "native_call_ms": sum(native) if native and all(x is not None for x in native) else None,
                             "performance_verdict": "not_evaluated"}}
