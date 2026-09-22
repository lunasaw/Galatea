from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from wechat_persona.topic_candidates import load_policy
from wechat_persona.topic_context import TopicContractError
from wechat_persona.topic_review_protocol import validate_axes
import test_topic_candidates as fixtures


class ReviewProtocolTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.TopicCandidateTests()
        fixture.setUp()
        fixture.config = load_policy(ROOT / 'configs/daily-topic-sft-v3.yaml')
        self.row = fixture.candidate()
        self.axes = {'relation': 'direct_answer', 'context_status': 'sufficient', 'communicative_value': 'useful',
                     'risk_status': 'clear', 'risk_flags': [], 'confidence': .95,
                     'responds_to_indices': [2], 'required_context_indices': [0, 1, 2]}

    def parse(self, **changes):
        return validate_axes(self.row, {**self.axes, **changes})

    def test_non_question_social_and_grounded_followup_can_be_kept(self):
        for relation in ('direct_answer', 'acknowledgement', 'social_response', 'grounded_followup'):
            result = self.parse(relation=relation)
            self.assertEqual(result['status'], 'keep')
            self.assertFalse(result['human_review_completed'])

    def test_known_unlinked_is_rejected_but_unknown_is_deferred(self):
        for relation in ('self_continuation', 'unrelated'):
            result = self.parse(relation=relation, responds_to_indices=[])
            self.assertEqual((result['status'], result['reason']), ('reject', 'off_context'))
            self.assertTrue(result['context_complete'])
        self.assertEqual(self.parse(relation='undetermined', responds_to_indices=[])['status'], 'uncertain')

    def test_missing_required_context_cannot_be_rescued_by_high_confidence(self):
        for context in ('missing_referent', 'missing_media', 'missing_external', 'undetermined'):
            result = self.parse(context_status=context, confidence=1)
            self.assertEqual(result['status'], 'uncertain')
            self.assertTrue(result['reply_link_correct'])
            self.assertFalse(result['context_complete'])

    def test_hard_risk_is_preserved_and_rejected_even_with_low_confidence(self):
        result = self.parse(risk_status='flagged', risk_flags=['privacy', 'third_party'], confidence=.4)
        self.assertEqual(result['status'], 'reject')
        self.assertEqual(result['axes']['risk_flags'], ['privacy', 'third_party'])
        self.assertEqual(self.parse(risk_status='undetermined')['status'], 'uncertain')

    def test_original_confidence_threshold_remains(self):
        self.assertEqual(self.parse(confidence=.899)['status'], 'uncertain')
        self.assertEqual(self.parse(confidence=.9)['status'], 'keep')

    def test_inconsistent_risk_or_anchor_fields_fail_closed(self):
        for change in ({'risk_flags': ['privacy']}, {'risk_status': 'flagged'},
                       {'responds_to_indices': []}, {'responds_to_indices': [1]},
                       {'responds_to_indices': [99]}, {'responds_to_indices': [True]},
                       {'required_context_indices': [0]}, {'relation': 'unrelated'}, {'status': 'keep'}):
            with self.subTest(change=change), self.assertRaises(TopicContractError):
                self.parse(**change)

    def test_candidate_and_axes_are_not_rewritten(self):
        original = copy.deepcopy(self.row)
        axes = copy.deepcopy(self.axes)
        result = self.parse()
        self.assertEqual(self.row, original)
        self.assertEqual(self.axes, axes)
        self.assertEqual(result['candidate_sha256'], self.row['candidate_sha256'])

    def test_changed_candidate_is_rejected_before_label_binding(self):
        self.row['messages'][-1]['content'] = 'tampered'
        with self.assertRaisesRegex(TopicContractError, 'candidate binding'):
            self.parse()


if __name__ == '__main__':
    unittest.main()
