"""选择、分组和保护的行为合同；输入为合成列表。"""
import unittest
from cpu_memory_cleanup.api import ProcessTarget
from cpu_memory_cleanup.application.targets import (select_rows, executable_groups, plan_targets, target_key)


class TargetTests(unittest.TestCase):
    def test_search_sort_does_not_change_identity_selection(self):
        rows = [{"pid": 11, "created_at": "1", "name": "a", "working_set_bytes": 40},
                {"pid": 12, "created_at": "2", "name": "b", "working_set_bytes": 90}]
        selected = {(11, "1")}
        visible = select_rows(rows)
        self.assertEqual([r["pid"] for r in visible], [12, 11])
        self.assertEqual([r["pid"] for r in visible if (r["pid"], r["created_at"]) in selected], [11])
        self.assertEqual(select_rows(rows, "12")[0]["name"], "b")

    def test_full_path_group_keeps_same_name_other_path_separate(self):
        rows = [{"pid": p, "created_at": str(p), "name": "app.exe", "image_path": path}
                for p, path in [(11, r"C:\A\app.exe"), (12, r"c:\a\APP.exe"), (13, r"C:\B\app.exe")]]
        self.assertEqual([len(g) for g in executable_groups(rows)], [2, 1])

    def test_protection_matches_path_or_instance_not_reused_pid(self):
        planned, _ = plan_targets([ProcessTarget(11, "2", image_path=r"C:\B\x.exe"),
                                   ProcessTarget(12, "3", image_path=r"C:\A\app.exe"),
                                   ProcessTarget(13, "4")],
                                  [{"pid": 11, "created_at": "1"}, {"image_path": r"C:\A\app.exe"}])
        self.assertFalse(planned[0]["reason"])
        self.assertIn("保护", planned[1]["reason"])
        self.assertIn("路径未知", planned[2]["reason"])

    def test_duplicate_target_is_rejected_before_execution(self):
        target = ProcessTarget(11, "1")
        with self.assertRaises(ValueError):
            plan_targets([target, target], [])
