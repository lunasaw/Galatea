from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from wechat_persona.topic_axes_review import fixture_candidate
from wechat_persona.topic_context import TopicContractError
from wechat_persona.topic_review_protocol_v4 import validate_axes


class UncertaintyWitnessTests(unittest.TestCase):
    def setUp(self):
        self.row = fixture_candidate({'id': 'witness-fixture',
            'past': [['self', '刚才说的那个安排能行吗？']], 'reply': '可以，就这样办。'})
        self.axes = {'relation': 'acknowledgement', 'context_status': 'missing_external',
                     'communicative_value': 'useful', 'risk_status': 'clear', 'risk_flags': [],
                     'confidence': .99, 'responds_to_indices': [0], 'required_context_indices': [0],
                     'uncertainties': [{'axis': 'context', 'issue': 'missing_external',
                                        'source_indices': [0], 'reply_quote': '这样办'}]}

    def test_high_confidence_does_not_erase_explicit_missing_evidence(self):
        result = validate_axes(self.row, self.axes)
        self.assertEqual(result['status'], 'uncertain')
        self.assertFalse(result['context_complete'])
        self.assertFalse(result['formal_training_eligible'])

    def test_old_responses_missing_witness_field_are_not_adopted(self):
        axes = {k: v for k, v in self.axes.items() if k != 'uncertainties'}
        with self.assertRaises(TopicContractError):
            validate_axes(self.row, axes)

    def test_rejects_fabricated_quote_index_axis_and_contradictions(self):
        changes = [
            {'reply_quote': '编造的原话'}, {'reply_quote': ''},
            {'source_indices': [1]}, {'source_indices': [True]}, {'source_indices': []},
            {'axis': 'risk', 'issue': 'unresolved_risk'}, {'issue': 'missing_media'},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(TopicContractError):
                axes = copy.deepcopy(self.axes)
                axes['uncertainties'][0].update(change)
                validate_axes(self.row, axes)
        for overrides in ({'uncertainties': []}, {'context_status': 'sufficient'},
                          {'uncertainties': self.axes['uncertainties'] * 2},
                          {'required_context_indices': []}):
            with self.subTest(overrides=overrides), self.assertRaises(TopicContractError):
                validate_axes(self.row, {**self.axes, **overrides})

    def test_all_unresolved_axes_need_separate_witnesses(self):
        axes = {**self.axes, 'relation': 'undetermined', 'responds_to_indices': [],
                'communicative_value': 'undetermined', 'risk_status': 'undetermined'}
        with self.assertRaises(TopicContractError):
            validate_axes(self.row, axes)
        axes['uncertainties'] = [*self.axes['uncertainties'], *[
            {'axis': a, 'issue': i, 'source_indices': [0], 'reply_quote': '可以'}
            for a, i in [('link', 'no_unique_self_anchor'), ('value', 'unclear_communicative_act'),
                         ('risk', 'unresolved_risk')]]]
        self.assertEqual(validate_axes(self.row, axes)['status'], 'uncertain')

    def test_source_tampering_still_rejected(self):
        row = copy.deepcopy(self.row)
        row['context_messages'][0]['content'] = 'changed'
        with self.assertRaises(TopicContractError):
            validate_axes(row, self.axes)


if __name__ == '__main__':
    unittest.main()
