"""显示摘要仅解释原报告，不连接系统。"""
import unittest
from cpu_memory_cleanup.contracts import RunReport, Sample
from cpu_memory_cleanup.interaction.presentation import summary

class PresentationTests(unittest.TestCase):
    def partial_sweep_report(self):
        return RunReport("run", "maintain", completion="partial",
            action={"attempted": True, "call_outcome": "failure", "status": 5, "privilege_restored": True,
                "process_sweep": {"successful_count": 692, "failed_count": 7, "skipped_count": 10,
                    "unknown_count": 0, "resources_closed": True}},
            samples=[Sample("run", i, None, {"physical": {"available_bytes": v * 1024**3}})
                for i, v in enumerate((14, 20))],
            errors=[{"phase": "calling", "message": "维护或恢复存在错误，见动作事实"}],
            metadata={"terminal": {"completion": "partial", "sample_count": 2, "errors": [{"phase": "calling"}]}})

    def test_completed_partial_sweep_keeps_real_delta_and_failed_counts(self):
        report = self.partial_sweep_report()
        original = report.to_dict()
        view = summary(report)
        self.assertEqual(view["title"], "已整理 · 可用内存增加 6.00 GiB")
        self.assertTrue(view["limited"])
        self.assertIn("逐进程成功692、跳过10、失败7", view["note"])
        self.assertIn("原生错误保留", view["note"])
        self.assertEqual(report.to_dict(), original)

    def test_partial_sweep_replay_uses_same_summary_without_hiding_source_gaps(self):
        report = self.partial_sweep_report()
        report.mode = "review"
        report.errors = [{"phase": "source", "message": "原运行条件或结果不完整"}]
        report.metadata["source_gaps"] = ["维护运行未完整成功或恢复未确认", "运行未完整成功"]
        self.assertIn("已整理 · 可用内存增加", summary(report)["title"])
        report.metadata["source_gaps"].append("缺样或重复序号")
        self.assertNotIn("可用内存增加", summary(report)["title"])
        self.assertFalse(summary(report)["limited"])

    def test_unconfirmed_sweep_close_does_not_use_completed_partial_summary(self):
        report = self.partial_sweep_report()
        report.action["process_sweep"]["resources_closed"] = False
        self.assertNotIn("可用内存增加", summary(report)["title"])
        report.action["process_sweep"]["resources_closed"] = True
        report.action["process_sweep"]["unknown_count"] = 1
        self.assertNotIn("可用内存增加", summary(report)["title"])

    def test_sweep_reports_coverage_separately_from_capacity_change(self):
        report = RunReport("run", "maintain", completion="success",
            action={"attempted": True, "call_outcome": "success", "process_sweep": {
                "successful_count": 2, "skipped_count": 1, "failed_count": 0}})
        view = summary(report)
        self.assertIn("逐进程成功2、跳过1、失败0", view["note"])
        self.assertNotIn("全机清空", view["note"])

    def test_observation_feedback_is_not_a_stability_claim(self):
        report = RunReport("run", "maintain", completion="success", action={"attempted":True,"call_outcome":"success"},
            samples=[Sample("run",i,None,{"physical":{"available_bytes":v*1024**3}}) for i,v in enumerate((2,3,4,3,5))],
            metadata={"header":{"post_observation_seconds":3}})
        view = summary(report)
        self.assertIn("观察结束", view["title"])
        self.assertIn("增加 3.00 GiB", view["title"])
        self.assertIn("不代表已稳定", view["note"])

    def test_signed_change_and_recorded_flow_time(self):
        report = RunReport("run", "maintain", completion="success", action={"attempted":True,"call_outcome":"success"},
            samples=[Sample("run",i,None,{"physical":{"available_bytes":v*1024**3}}) for i,v in enumerate((3,2))],
            metadata={"header":{"started_at":"2026-10-31T00:00:00+00:00","mechanism":"working-set"},
                      "terminal":{"finished_at":"2026-10-31T00:00:02+00:00"}})
        view=summary(report)
        self.assertIn("减少 1.00 GiB",view["title"])
        self.assertIn("本次流程2.0秒",view["context"])
        self.assertEqual(report.samples[0].metrics["physical"]["available_bytes"],3*1024**3)

    def test_failure_reason_is_visible_without_fabricated_time(self):
        report=RunReport("run","maintain",completion="failed",errors=[{"message":"记录目录不可写"}])
        view=summary(report)
        self.assertIn("未派发",view["title"])
        self.assertEqual(view["note"],"记录目录不可写")
        self.assertNotIn("秒",view["context"])

    def test_batch_summary_does_not_add_working_sets_or_claim_no_dispatch(self):
        report=RunReport("run","batch",completion="partial",metadata={"items":[{"status":"success"},{"status":"skipped"},{"status":"failed"}]})
        view=summary(report)
        self.assertIn("逐项整理",view["title"])
        self.assertIn("成功1 · 跳过1",view["note"])
        self.assertIn("不累加",view["note"])
