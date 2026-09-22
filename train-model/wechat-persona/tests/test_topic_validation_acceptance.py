from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona import topic_validation_acceptance as acceptance
from wechat_persona import topic_validation as preparation
from wechat_persona.topic_context import TopicContractError


def label(**overrides):
    return {'relation': 'linked', 'context': 'sufficient', 'value': 'useful', 'risk': 'clear',
            'responds_to_indices': [0], 'missing_evidence_indices': [], 'issues': [], **overrides}


class AcceptanceTests(unittest.TestCase):
    def fixture(self):
        candidates, cases, decisions, references = [], [], [], []
        for i, group in enumerate(['keep', 'reject', 'uncertain']):
            sid = str(i)
            candidates.append({'sample_id': sid, 'candidate_sha256': sid * 64,
                'context_message_ids': ['s'], 'reply_link': {'responds_to_ids': ['s']}})
            cases.append({'sample_id': sid, 'candidate_sha256': sid * 64,
                'strata': ['ordinary'], 'shared_responds_to_ids': ['s'],
                'disposition': 'machine_consensus_keep' if group == 'keep' else 'axes_keep_not_agreed'})
            decisions.extend({'sample_id': sid, 'judge': judge, 'status': group} for judge in ('gpt', 'claude'))
            references.append({'sample_id': sid, 'candidate_sha256': sid * 64,
                'selected_source_ids': ['s'], 'selected': {'label': label()}, 'expanded': {'label': label()}})
        return candidates, cases, decisions, references

    def test_unknown_and_zero_denominators(self):
        result = acceptance.proportion([True, None, False])
        self.assertEqual(result['numerator'], 1)
        self.assertEqual(result['denominator'], 3)
        self.assertEqual(result['unknown'], 1)
        self.assertAlmostEqual(result['rate'], 1 / 3)
        self.assertFalse(acceptance.proportion([None])['estimable'])
        self.assertIsNone(acceptance.proportion([None])['rate'])
        self.assertIsNone(acceptance.proportion([])['rate'])
        self.assertIsNone(acceptance.proportion([])['wilson_95_descriptive'])

    def test_reference_audit_reads_only_approved_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'messages.jsonl'
            path.write_text('\n'.join(json.dumps({'message_id': mid, 'reply_to': ref, 'text_redacted': 'secret'})
                for mid, ref in [('a', None), ('b', 'a'), ('test', 'future')]))
            original = json.loads
            def metadata_only(text):
                self.assertFalse(text.startswith('{'))
                return original(text)
            with patch.object(acceptance.json, 'loads', side_effect=metadata_only):
                report = acceptance.count_reference_metadata(path, {'a': 'train', 'b': 'validation', 'test': 'test'})
            self.assertEqual(report['by_split']['train']['nonnull_reply_to'], 0)
            self.assertEqual(report['by_split']['validation']['nonnull_reply_to'], 1)
            self.assertFalse(report['test_reference_fields_scanned'])
            with self.assertRaises(TopicContractError):
                acceptance.count_reference_metadata(path, {'missing': 'validation'})

    def test_missing_reference_and_expanded_only_evidence_never_become_support(self):
        candidates, _, _, refs = self.fixture()
        self.assertIsNone(acceptance.reference_values(candidates[0], None, ['s'])['machine_keep_support'])
        ref = deepcopy(refs[0])
        ref['expanded']['label'].update(context='insufficient', missing_evidence_indices=[1])
        values = acceptance.reference_values(candidates[0], ref, ['s'])
        self.assertTrue(values['reply_link_support'])
        self.assertFalse(values['input_completeness_support'])
        self.assertFalse(values['machine_keep_support'])
        ref['expanded']['label'].update(relation='unknown', responds_to_indices=[])
        self.assertIsNone(acceptance.reference_values(candidates[0], ref, ['s'])['reply_link_support'])

    def test_atomic_anchors_and_known_negatives(self):
        candidates, _, _, refs = self.fixture()
        candidates[0]['reply_link']['responds_to_ids'] = ['different']
        values = acceptance.reference_values(candidates[0], refs[0], ['different'])
        self.assertFalse(values['reply_link_support'])
        self.assertFalse(values['machine_keep_support'])
        self.assertTrue(values['usable'])
        self.assertFalse(acceptance.conjunction([None, False, True]))

    def test_reject_and_uncertain_denominators_remain_separate(self):
        candidates, cases, decisions, refs = self.fixture()
        policy = preparation.load_policy(ROOT / 'configs/topic-validation-v1.yaml')
        refs[1]['expanded']['label']['context'] = 'insufficient'
        refs[2]['expanded'] = None
        report = acceptance.summarize(candidates, cases, decisions, refs, policy)
        metrics = report['overall']['metrics']
        self.assertEqual(metrics['machine_keep_support']['numerator'], 1)
        self.assertEqual(metrics['machine_keep_support']['denominator'], 1)
        self.assertEqual(metrics['erroneous_reject_support']['denominator'], 1)
        self.assertEqual(metrics['erroneous_reject_support']['numerator'], 0)
        self.assertEqual(metrics['uncertain_usable_support']['denominator'], 1)
        self.assertEqual(metrics['uncertain_usable_support']['unknown'], 1)
        self.assertIn('explicit_reference', report['insufficient_strata'])
        self.assertFalse(report['numeric_machine_support_gates_passed'])
        refs[0]['candidate_sha256'] = 'changed'
        with self.assertRaises(TopicContractError):
            acceptance.summarize(candidates, cases, decisions, refs, policy)


if __name__ == '__main__':
    unittest.main()
