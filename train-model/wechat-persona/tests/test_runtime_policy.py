import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.runtime import RuntimeEligibilityError, check_runtime_eligibility, safe_chat_response


class RuntimePolicyTests(unittest.TestCase):
    def test_only_accepted_reviewed_nonwithdrawn_candidate_can_enable(self):
        accepted = {"quality_evidence_status": "accepted", "human_review_completed": True, "withdrawn": False, "protocol_digest": "p"}
        self.assertTrue(check_runtime_eligibility(accepted, "p"))
        for bad in (
            {**accepted, "quality_evidence_status": "experimental_only"},
            {**accepted, "human_review_completed": False},
            {**accepted, "withdrawn": True},
            {**accepted, "protocol_digest": "other"},
        ):
            with self.assertRaises(RuntimeEligibilityError):
                check_runtime_eligibility(bad, "p")

    def test_chat_has_ai_identity_and_memory_can_be_disabled(self):
        response = safe_chat_response("hello", owner_scope="owner-a", memory_enabled=False, generator=lambda _: "reply")
        self.assertTrue(response.startswith("AI 生成内容"))
        self.assertEqual([], response.metadata["memory_ids"])

    def test_failed_output_is_safe_fallback(self):
        response = safe_chat_response("hello", owner_scope="owner-a", memory_enabled=False, generator=lambda _: "<SECRET>")
        self.assertTrue(response.fallback)
        self.assertNotIn("SECRET", response.text.upper())


if __name__ == "__main__":
    unittest.main()
