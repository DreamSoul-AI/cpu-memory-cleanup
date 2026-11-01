"""规则只对合成时间与指标作决定，不接入系统。"""
import unittest
from cpu_memory_cleanup.application.policy import Policy, Rule


class PolicyTests(unittest.TestCase):
    def test_interval_requires_explicit_arm_and_honors_cooldown(self):
        policy = Policy(Rule(interval_s=10, cooldown_s=20))
        self.assertFalse(policy.due(20))
        with self.assertRaises(ValueError):
            policy.arm(0)
        policy.arm(0, confirmed=True, source="test:user")
        self.assertFalse(policy.due(10))
        self.assertTrue(policy.due(20))
        policy.dispatched(20)
        self.assertFalse(policy.due(30))
        policy.stop()
        self.assertFalse(policy.due(100))

    def test_pressure_needs_continuity_and_resets_on_missing_data(self):
        policy = Policy(Rule(mode="pressure", sustain_s=3, cooldown_s=1))
        policy.arm(0, confirmed=True, source="test:user")
        self.assertFalse(policy.due(1, available_pct=5))
        self.assertFalse(policy.due(2, available_pct=None))
        self.assertFalse(policy.due(3, available_pct=5))
        self.assertTrue(policy.due(6, available_pct=5))
        self.assertFalse(policy.due(7, available_pct=5, busy=True))

    def test_delayed_observation_does_not_prove_sustained_pressure(self):
        policy = Policy(Rule(mode="pressure", sustain_s=3, cooldown_s=1))
        policy.arm(0, confirmed=True, source="test:user")
        policy.due(1, available_pct=5)
        self.assertFalse(policy.due(20, available_pct=5))
        with self.assertRaises(ValueError):
            policy.due(19, available_pct=5)
        self.assertFalse(policy.armed)
