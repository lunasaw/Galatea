import tempfile
import unittest
from pathlib import Path

from wechat_persona.evaluation import (
    EvaluationGovernanceError,
    VARIANT_NAMES,
    claim_test_once,
    evaluate_variant_matrix,
    freeze_candidate,
    validate_safety_gates,
)


class EvaluationGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.protocol = {
            "protocol_version": "wechat-persona-style-v1",
            "split": "validation",
            "split_sha256": "a" * 64,
            "prompt_digest": "b" * 64,
            "generation_digest": "c" * 64,
            "index_digest": "d" * 64,
            "seed": 42,
        }

    def test_five_variants_share_one_frozen_protocol(self):
        records = {
            name: [{"case_id": "case-1", "output": "好的", "latency_ms": 1.0}]
            for name in VARIANT_NAMES
        }
        report = evaluate_variant_matrix(records, self.protocol)
        self.assertEqual(tuple(report["variants"]), VARIANT_NAMES)
        self.assertEqual(report["case_ids"], ["case-1"])

    def test_candidate_freeze_requires_validation_and_hard_gates(self):
        candidate = {
            "run_id": "run-1",
            "variant": "lora",
            "adapter_sha256": "e" * 64,
            "validation_metrics": {"lora_vs_prompt_only_win_rate": 0.61},
            "artifact_roundtrip_verified": True,
            "safety_metrics": {
                "pii_leak_count": 0,
                "canary_leak_count": 0,
                "unsafe_behavior_count": 0,
                "impersonation_count": 0,
                "dependency_manipulation_count": 0,
                "crash_rate": 0.0,
                "empty_output_rate": 0.0,
            },
        }
        frozen = freeze_candidate(candidate, self.protocol)
        self.assertEqual(frozen["test_access"], "untouched")
        self.assertEqual(len(frozen["candidate_freeze_id"]), 64)
        with self.assertRaises(EvaluationGovernanceError):
            freeze_candidate({**candidate, "test_metrics": {"score": 1}}, self.protocol)

    def test_test_once_is_atomic_and_protocol_bound(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = Path(temp_dir) / "test-once.json"
            claim = claim_test_once("f" * 64, self.protocol, ledger)
            self.assertEqual(len(claim["test_evaluation_id"]), 64)
            with self.assertRaises(EvaluationGovernanceError):
                claim_test_once("f" * 64, self.protocol, ledger)

    def test_safety_hard_gate_is_zero_tolerance(self):
        with self.assertRaisesRegex(EvaluationGovernanceError, "pii_leak_count"):
            validate_safety_gates({"pii_leak_count": 1})


if __name__ == "__main__":
    unittest.main()
