import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.governance import GovernanceError, advance_state, block_state, withdraw_state


class StateMachineTests(unittest.TestCase):
    def test_state_cannot_skip(self):
        state = {"status": "DISCOVERED", "history": []}
        with self.assertRaises(GovernanceError): advance_state(state, "IMPORTED")
        state = advance_state(state, "CONSENT_VERIFIED")
        self.assertEqual("CONSENT_VERIFIED", state["status"])

    def test_block_codes_are_structured_and_withdrawal_invalidates_downstream(self):
        with self.assertRaises(GovernanceError): block_state({"status": "DISCOVERED"}, "free text")
        blocked = block_state({"status": "DISCOVERED"}, "consent_missing")
        self.assertEqual("BLOCKED", blocked["status"])
        withdrawn = withdraw_state({"status": "CANDIDATE_FROZEN", "downstream_object_ids": ["adapter", "index"]}, "scope-1")
        self.assertEqual(["adapter", "index"], withdrawn["invalidated_object_ids"])


if __name__ == "__main__": unittest.main()
