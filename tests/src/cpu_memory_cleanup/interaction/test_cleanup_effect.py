import unittest
from cpu_memory_cleanup.interaction.cleanup_effect import CleanupEffect


class EffectTests(unittest.TestCase):
    def test_clock_only_changes_decoration_and_waits_for_notification(self):
        effect = CleanupEffect()
        effect.start(40, 0)
        self.assertEqual(effect.tick(1.5), 20)
        self.assertEqual(effect.tick(3), 0)
        effect.tick(20)
        self.assertEqual(effect.phase, "waiting")
        effect.finish(30)
        effect.tick(21)
        effect.tick(22.3)
        self.assertIsNone(effect.phase)
        self.assertEqual(effect.value, 30)

    def test_stop_drops_only_visual_state(self):
        effect = CleanupEffect()
        effect.start(40, 0)
        effect.stop()
        self.assertIsNone(effect.phase)
        self.assertEqual(effect.value, 0)
