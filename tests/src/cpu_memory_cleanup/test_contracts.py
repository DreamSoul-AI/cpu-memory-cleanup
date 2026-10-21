import unittest

from cpu_memory_cleanup.contracts import ActionRequest, Capabilities, Sample, validate_action


class ContractTests(unittest.TestCase):
    def sample(self, value=0):
        return Sample("run", 0, "2026-10-21T00:00:00+00:00", {"physical": {"available_bytes": value}})

    def test_zero_is_preserved(self):
        self.assertEqual(self.sample().metrics["physical"]["available_bytes"], 0)

    def test_unknown_is_preserved(self):
        self.assertIsNone(self.sample(None).metrics["physical"]["available_bytes"])

    def test_nan_is_rejected(self):
        with self.assertRaises(ValueError):
            self.sample(float("nan"))

    def test_boolean_metric_is_rejected(self):
        with self.assertRaises(ValueError):
            self.sample(True)

    def test_timezone_is_required(self):
        with self.assertRaises(ValueError):
            Sample("r", 0, "2026-10-21T00:00:00", {})

    def test_unknown_legacy_timestamp_is_not_fabricated(self):
        self.assertIsNone(Sample("r", 0, None, {}).timestamp)

    def test_signed_residual_is_preserved(self):
        sample = Sample("r", 0, None, {"commit_residual": {"residual_gib": -0.5}})
        self.assertEqual(sample.metrics["commit_residual"]["residual_gib"], -0.5)

    def test_huge_integer_is_a_validation_error(self):
        with self.assertRaises(ValueError):
            self.sample(10 ** 1000)

    def test_combined_mechanism_is_rejected(self):
        with self.assertRaises(ValueError):
            ActionRequest("both", confirmed=True, confirmation_source="test")

    def test_confirmation_cannot_be_truthy_text(self):
        with self.assertRaises(ValueError):
            ActionRequest("standby", confirmed="yes")

    def test_single_mechanism_is_not_bound_to_one_operating_system(self):
        request = ActionRequest("application-cooperative", confirmed=True, confirmation_source="test")
        self.assertEqual(request.mechanism, "application-cooperative")

    def test_capability_metadata_rejects_invalid_mechanism_items(self):
        with self.assertRaises(ValueError):
            Capabilities("fake", mechanisms=({},))

    def test_generic_call_fact_preserves_opaque_platform_status(self):
        fact = {"attempted": True, "call_outcome": "success", "status": "platform-ok", "privilege_restored": True}
        self.assertEqual(validate_action(fact), fact)
