"""批次复用既有单次维护；索引不重写子动作事实。"""

from dataclasses import asdict
import threading

from ..contracts import ProcessTarget, StageTimings
from .targets import plan_targets
from . import verification


def execute(app, targets, output_dir, *, protections=(), confirmed=False, confirmation_source="", cancel_event=None):
    report = app._report("batch")
    journal = None
    admitted = False
    timings = StageTimings(app._monotonic)
    try:
        if confirmed is not True or not isinstance(confirmation_source, str) or not confirmation_source.strip():
            raise ValueError("批次必须有明确确认及来源")
        if cancel_event is not None and not isinstance(cancel_event, threading.Event):
            raise ValueError("取消信号必须是线程事件")
        planned, rules = plan_targets(targets, list(protections))
        with app._lock:
            if app._active or app._batch_active or app._unsafe or app._closed:
                raise ValueError("当前忙碌或安全上下文未就绪")
            app._batch_active, app._batch_thread = True, threading.get_ident()
            app._batch_cancel = cancel_event or threading.Event()
            admitted = True
        journal = app.records.open_batch(output_dir, report.run_id)
        report.output_dir = str(journal.directory)
        header = {"record_type": "batch_header", "batch_schema_version": 1, "run_id": report.run_id,
                  "targets": [row["target"] for row in planned], "protections": rules,
                  "confirmation_source": confirmation_source}
        report.metadata = {"header": header, "items": []}
        journal.append(header)
        for index, row in enumerate(planned):
            item = {"index": index, "target": row["target"], "status": "skipped", "reason": row["reason"]}
            if app._batch_cancel.is_set() or app._closed or app._unsafe:
                item.update(status="not_started", reason="批次停止或安全上下文未恢复")
            elif not row["reason"]:
                # 意图先保存。中断留下意图时回看必须解释为结果未知，不能自动重试。
                journal.append({"record_type": "item_intent", "index": index})
                child = app.memory_process_cleanup(ProcessTarget(**row["target"]), journal.directory,
                    confirmed=True, confirmation_source=confirmation_source, cancel_event=app._batch_cancel)
                item.update(status=child.completion, reason="", run_id=child.run_id,
                            action=child.action, errors=child.errors, analysis=child.analysis,
                            has_record=child.output_dir is not None,
                            comparison_evidence=verification.child_evidence(child))
            report.metadata["items"].append(item)
            journal.append({"record_type": "item_result", "item": item})
        items = report.metadata["items"]
        report.cancel_requested = app._batch_cancel.is_set()
        report.completion = ("cancelled" if report.cancel_requested else
                             "success" if all(i["status"] in ("success", "skipped") for i in items) else "partial")
        report.analysis = {"mode": "batch-cleanup", "counts": {status: sum(i["status"] == status for i in items)
                           for status in sorted({i["status"] for i in items})},
                           "caveat": "逐项工作集变化不相加称为系统释放量或提速"}
        journal.append({"record_type": "batch_end", "completion": report.completion, "count": len(items)})
    except Exception as error:
        report.completion = "partial" if report.metadata.get("items") else "failed"
        report.errors.append({"phase": "batch", "type": type(error).__name__, "message": str(error)})
    finally:
        if journal is not None:
            try:
                journal.close()
            except Exception as error:
                report.errors.append({"phase": "batch_close", "message": str(error)})
                report.completion = "partial"
        if admitted:
            with app._lock:
                app._batch_active, app._batch_thread = False, None
        if report.metadata.get("header"):
            try:
                timings.mark("batch_closed")
                timing = timings.snapshot()
                value = {"version": 1, "kind": "batch", "run_id": report.run_id, "source": "live", "timing": timing}
                value["comparison"] = verification.batch_summary(report.metadata["items"],
                    selected_count=len(report.metadata["header"]["targets"]), timing=timing)
                report.analysis["comparison"] = value["comparison"]
                report.metadata["diagnostics"] = value
                app.records.write_diagnostics(report.output_dir, value)
            except Exception as error:
                report.metadata["diagnostics_errors"] = [{"type": type(error).__name__, "message": str(error)}]
        report.phase = "finished"
    return report


def review(app, directory):
    report = app._report("batch-review")
    try:
        records = app.records.read_batch(directory)
        if not records or records[0].get("record_type") != "batch_header" or records[0].get("batch_schema_version") != 1:
            raise ValueError("无效批次运行头")
        header = records[0]
        planned, _ = plan_targets([ProcessTarget(**r) for r in header["targets"]], header["protections"])
        items, pending, ended = [], None, False
        for record in records[1:]:
            if ended:
                raise ValueError("批次终态后有多余记录")
            kind = record.get("record_type")
            if kind == "item_intent":
                if pending is not None or record.get("index") != len(items):
                    raise ValueError("批次意图顺序无效")
                pending = len(items)
            elif kind == "item_result":
                item = dict(record["item"])
                # 摘要只从已核对的原子运行重建，不信任索引中的派生证据。
                item.pop("comparison_evidence", None)
                index = len(items)
                if index >= len(planned) or item.get("index") != index or item.get("target") != planned[index]["target"]:
                    raise ValueError("批次目标或顺序无效")
                if item.get("status") in ("skipped", "not_started"):
                    if pending is not None or item.get("action"):
                        raise ValueError("跳过项不应有动作")
                    if item["status"] == "skipped" and item.get("reason") != planned[index]["reason"]:
                        raise ValueError("保护依据与批次不一致")
                elif pending != index:
                    raise ValueError("动作缺少批次意图")
                else:
                    child_id = item.get("run_id")
                    if not isinstance(child_id, str) or not child_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in child_id):
                        raise ValueError("无效子记录引用")
                    if item.get("has_record"):
                        child_path = app.records.batch_child_path(directory, child_id)
                        child = app.review(child_path)
                        if (asdict(ProcessTarget(**child.metadata.get("header", {}).get("target", {}))) != item["target"]
                                or child.action != item.get("action")):
                            raise ValueError("子记录与批次动作不一致")
                        if child.completion != "success":
                            report.errors.append({"message": "子运行未完整成功", "run_id": child_id})
                        item["comparison_evidence"] = verification.child_evidence(child)
                    else:
                        item.pop("comparison_evidence", None)
                        report.errors.append({"message": "子运行没有可回看记录", "run_id": child_id})
                items.append(item)
                pending = None
            elif kind == "batch_end":
                if pending is not None or record.get("count") != len(items) or len(items) != len(planned):
                    raise ValueError("批次终态不完整")
                if record.get("completion") not in ("success", "partial", "cancelled"):
                    raise ValueError("批次终态无效")
                ended = True
            else:
                raise ValueError("未知批次记录")
        report.metadata = {"header": header, "items": items, "pending_index": pending}
        report.completion = "success" if ended and records[-1]["completion"] == "success" and not report.errors else "partial"
        if not ended:
            report.errors.append({"message": "批次未封存，未完成动作不得自动重放"})
        report.output_dir = str(directory)
        timing = None
        try:
            saved = app.records.read_diagnostics(directory)
            if saved is not None:
                verification.validate_attachment(saved, header["run_id"])
                if saved.get("kind") != "batch":
                    raise ValueError("批次诊断类型无效")
                timing = saved.get("timing")
                report.metadata["diagnostics"] = {**saved, "source": "replay"}
        except Exception as error:
            report.metadata["diagnostics_errors"] = [{"type": type(error).__name__, "message": str(error)}]
        report.analysis = {"mode": "batch-cleanup", "counts": {s: sum(i["status"] == s for i in items)
            for s in sorted({i["status"] for i in items})},
            "comparison": verification.batch_summary(items, selected_count=len(planned), timing=timing),
            "caveat": "逐项工作集变化不相加称为系统释放量或提速"}
        if "diagnostics" in report.metadata:
            report.metadata["diagnostics"]["comparison"] = report.analysis["comparison"]
    except Exception as error:
        report.errors.append({"phase": "batch_review", "type": type(error).__name__, "message": str(error)})
        report.completion = "failed"
    report.phase = "finished"
    return report
