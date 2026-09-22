import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from wechat_persona._common import digest
from wechat_persona.topic_context import TopicContractError
from wechat_persona.topic_evidence_export import bind_reply_evidence
import test_topic_evidence_audit as fixtures


class ReplyEvidenceBindingTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.EvidenceAuditTests()
        fixture.setUp()
        self.row = fixture.row
        self.decision = fixture.parse()[0]

    def test_sidecar_preserves_real_input_label_and_original_hypothesis(self):
        before = json.dumps(self.row, sort_keys=True)
        decision = {**self.decision, 'responds_to_indices': [0], 'responds_to_ids': ['m0']}
        evidence = bind_reply_evidence(self.row, decision, 'a' * 64)
        self.assertEqual(evidence['responds_to_ids'], ['m0'])
        self.assertEqual(evidence['original_responds_to_ids'], ['m2'])
        self.assertFalse(evidence['source_reply_to_claimed'])
        self.assertTrue(evidence['target_text_used_for_offline_verification'])
        self.assertFalse(evidence['target_text_used_for_context_selection'])
        self.assertEqual(json.dumps(self.row, sort_keys=True), before)

    def test_diagnostic_and_uncertain_decisions_cannot_be_compiled(self):
        for changes in ({'view_kind': 'expanded_diagnostic'}, {'status': 'uncertain'},
                        {'confidence': .89}, {'review_kind': 'human'}, {'context_complete': False},
                        {'sample_id': 'another-row'}):
            with self.subTest(changes=changes), self.assertRaisesRegex(TopicContractError, 'qualify'):
                bind_reply_evidence(self.row, {**self.decision, **changes}, 'a' * 64)

    def test_fabricated_indices_wrong_roles_and_noncausal_sources_fail_closed(self):
        for changes in ({'responds_to_indices': [50]}, {'responds_to_ids': ['missing']},
                        {'responds_to_indices': [1], 'responds_to_ids': ['m1']},
                        {'required_context_indices': [0], 'required_context_ids': ['m0']}):
            with self.subTest(changes=changes), self.assertRaises(TopicContractError):
                bind_reply_evidence(self.row, {**self.decision, **changes}, 'a' * 64)
        changed = copy.deepcopy(self.row)
        changed['context_messages'][0]['order'] = changed['cutoff']['source_record_index'] + 1
        changed['candidate_sha256'] = digest({key: value for key, value in changed.items() if key != 'candidate_sha256'})
        with self.assertRaisesRegex(TopicContractError, 'causal scope'):
            bind_reply_evidence(changed, {**self.decision, 'candidate_sha256': changed['candidate_sha256']}, 'a' * 64)

    def test_changed_input_digest_cannot_reuse_review(self):
        changed = copy.deepcopy(self.row)
        changed['messages'][1]['content'] += ' changed'
        with self.assertRaisesRegex(TopicContractError, 'invalid evidence draft candidate'):
            bind_reply_evidence(changed, self.decision, 'a' * 64)


if __name__ == '__main__':
    unittest.main()
