import unittest

from llm_lora_playground.evaluation import (
    EvaluationProtocolError,
    compute_automatic_metrics,
    freeze_candidate,
    run_fixed_style_checks,
)


class EvaluationProtocolTests(unittest.TestCase):
    def test_metrics_and_style_checks_are_deterministic(self):
        records = [{"output": "好的，温和地说。", "reference": "好的", "validation_loss": 0.5}]
        metrics = compute_automatic_metrics(records)
        self.assertEqual(metrics["record_count"], 1)
        self.assertIn("generated_length_mean", metrics)
        self.assertEqual(run_fixed_style_checks(records, "style-v1")["ruleset_version"], "style-v1")

    def test_test_metric_cannot_freeze_candidate(self):
        with self.assertRaises(EvaluationProtocolError):
            freeze_candidate({"run_id": "r", "test_loss": 0.1}, {"split_manifest_sha256": "x"})


if __name__ == "__main__":
    unittest.main()
