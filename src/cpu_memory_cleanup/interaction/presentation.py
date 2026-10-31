"""交互层共用的动作摘要；只解释已有报告，不读取系统或改写事实。"""
from datetime import datetime


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp if stamp.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def summary(report):
    header = report.metadata.get("header") or {}
    terminal = report.metadata.get("terminal") or {}
    batch = report.mode in ("batch", "batch-review")
    scope = "选中进程批次" if batch else "指定进程" if header.get("scope") == "process" else "系统工作集" if header.get("mechanism", "working-set") == "working-set" else "系统 Standby"
    completion = {"success": "完成", "partial": "结果不完整", "failed": "失败", "cancelled": "已取消"}.get(report.completion, "结果未知")
    outcome = {"success": "调用成功", "failure": "调用失败", "not_started": "未派发", "unknown": "调用结果未知"}.get(report.action.get("call_outcome"), "逐项结果")
    title = f"{completion} · {outcome}"
    note = "容量变化不代表永久释放或提速。"
    if batch:
        title = f"{completion} · 逐项整理"
        items = report.metadata.get("items", [])
        note = f"{len(items)}个目标 · 成功{sum(i['status']=='success' for i in items)} · 跳过{sum(i['status']=='skipped' for i in items)}；逐项变化不累加为系统释放量。"
    elif len(report.samples) >= 2:
        process = header.get("scope") == "process"
        values = [((s.metrics.get("target") or {}).get("working_set_bytes") if process else
                   (s.metrics.get("physical") or {}).get("available_bytes")) for s in (report.samples[0], report.samples[-1])]
        if all(v is not None for v in values):
            before, after = values
            delta = after - before
            label = "目标工作集" if process else "可用内存"
            change_title = f"{completion} · {label}{'增加' if delta>0 else '减少' if delta<0 else '不变'}" + (f" {abs(delta)/1024**3:.2f} GiB" if delta else "")
            if report.action.get("call_outcome") == "success":
                title = change_title
            note = f"{before/1024**3:.2f} → {after/1024**3:.2f} GiB · 再次使用时占用可能回升。"
    if report.errors:
        note = str(report.errors[0].get("message") or report.errors[0].get("reason") or "详情中查看错误")
    end = _stamp(terminal.get("finished_at"))
    start = _stamp(header.get("started_at"))
    when = end.astimezone().strftime("%H:%M:%S") if end else "时间未记录"
    duration = f" · 本次流程{(end-start).total_seconds():.1f}秒" if start and end and end >= start else ""
    return {"title": title, "note": note, "context": f"{scope} · {when}{duration}", "scope": scope}
