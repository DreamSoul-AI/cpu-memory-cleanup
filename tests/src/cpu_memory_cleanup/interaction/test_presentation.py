"""显示摘要仅解释原报告，不连接系统。"""
import unittest
from cpu_memory_cleanup.contracts import RunReport, Sample
from cpu_memory_cleanup.interaction.presentation import summary

class PresentationTests(unittest.TestCase):
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
