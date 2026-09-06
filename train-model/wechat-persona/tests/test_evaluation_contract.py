import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.evaluation import (
    EvaluationContractError, FIVE_VARIANTS, blind_preference_report,
    capacity_decision, claim_test_once, freeze_candidate, validate_variant_matrix,
)


class EvaluationContractTests(unittest.TestCase):
    def test_five_variants_share_protocol_and_candidate_uses_validation_only(self):
        rows = [{"variant": variant, "protocol_digest": "p", "split_sha256": "s", "generation_digest": "g", "input_digest": "i"} for variant in FIVE_VARIANTS]
        validate_variant_matrix(rows)
        frozen = freeze_candidate({"run_id": "r1", "validation": {"lora_vs_prompt_only_win_rate": 0.65}, "adapter_sha256": "a" * 64, "artifact_roundtrip_verified": True, "safety_metrics": {}}, {"protocol_digest": "p", "split_sha256": "s", "generation_digest": "g", "split": "validation"})
        self.assertEqual("untouched", frozen["test_access"])
        with self.assertRaises(EvaluationContractError):
            freeze_candidate({"run_id": "r", "validation": {}, "final_test": {"score": 1}}, {})

    def test_test_once_is_atomic(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "claims.json"
            first = claim_test_once("freeze", "s" * 64, ledger)
            self.assertEqual(64, len(first["test_evaluation_id"]))
            with self.assertRaises(EvaluationContractError):
                claim_test_once("freeze", "s" * 64, ledger)

    def test_blind_preference_and_capacity_stop_decision(self):
        report = blind_preference_report(["lora"] * 60 + ["prompt-only"] * 30 + ["tie"] * 10)
        self.assertEqual(0.6, report["lora_vs_prompt_only_win_rate"])
        self.assertEqual("stop", capacity_decision(0.64, 0.67, min_delta=0.05, within_budget=True)["decision"])


if __name__ == "__main__":
    unittest.main()
