import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from wechat_persona._common import digest, file_digest
from wechat_persona.topic_candidates import load_policy, write_json
from wechat_persona.topic_comparison import compare_population, report_context_repair
from wechat_persona.topic_context import TopicContractError
import test_topic_candidates as fixtures


class TopicComparisonTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.TopicCandidateTests()
        fixture.setUp()
        self.row = fixture.candidate()
        self.decision = {'status': 'keep', 'reply_link_correct': True, 'context_complete': True}
        self.decisions = {self.row['sample_id']: self.decision}

    def test_missing_target_cannot_silently_shrink_denominator(self):
        with self.assertRaisesRegex(TopicContractError, 'every frozen target'):
            compare_population([self.row], [], self.decisions, {}, [])
        result = compare_population([self.row], [], self.decisions, {},
                                    [{'target_message_ids': self.row['target_message_ids']}])
        self.assertEqual(result['metrics']['reply_link_correct']['after']['denominator'], 1)
        self.assertEqual(result['metrics']['reply_link_correct']['after']['rate'], 0)
        self.assertEqual(result['quarantined_after'], 1)

    def test_unchanged_prompt_judge_disagreement_is_separate(self):
        decisions = {self.row['sample_id']: {**self.decision, 'context_complete': False}}
        result = compare_population([self.row], [self.row], self.decisions, decisions, [])
        groups = result['metrics']['context_complete']['paired_groups']
        self.assertEqual(groups['unchanged_input']['true_to_false'], 1)
        self.assertEqual(groups['changed_input']['count'], 0)
        self.assertFalse(result['quality_gate_passed'])

    def test_changed_real_target_or_duplicate_population_is_rejected(self):
        changed = copy.deepcopy(self.row)
        changed['messages'][-1]['content'] = '不同答案'
        with self.assertRaisesRegex(TopicContractError, 'target or prompt contract'):
            compare_population([self.row], [changed], self.decisions, self.decisions, [])
        with self.assertRaisesRegex(TopicContractError, 'every frozen target'):
            compare_population([self.row], [self.row, self.row], self.decisions, self.decisions, [])

    def test_context_restoration_does_not_transfer_diagnostic_quality_labels(self):
        fixture = fixtures.TopicCandidateTests()
        fixture.setUp()
        fixture.config = load_policy(ROOT / 'configs/daily-topic-sft-v2.yaml')
        repaired = fixture.candidate()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before, after, audit = root / 'before', root / 'after', root / 'audit'
            for path in (before, after, audit):
                path.mkdir()
            write_json(before / 'manifest.json', {})
            parent_sha = file_digest(before / 'manifest.json')
            new_manifest = {'identity': {'reference_pilot_manifest_sha256': parent_sha}}
            write_json(after / 'manifest.json', new_manifest)
            write_json(audit / 'report.json', {'status': 'complete', 'diagnosis_cases': [{
                'sample_id': self.row['sample_id'], 'cause': 'machine_suggested_context_omission',
                'missing_evidence_ids': ['m0']}]})
            audit_manifest = {'identity': {'pilot_manifest_sha256': parent_sha},
                              'output_digests': {'report.json': file_digest(audit / 'report.json')}}
            audit_manifest['manifest_sha256'] = digest(audit_manifest)
            write_json(audit / 'manifest.json', audit_manifest)
            def verified(path):
                return ({}, [self.row]) if path == before else (new_manifest, [repaired])
            with patch('wechat_persona.topic_comparison.verified_pilot', side_effect=verified):
                result = report_context_repair(before=before, after=after, audit=audit,
                                               output_root=root / 'reports', controlled_root=root)['report']
                self.assertEqual(result['fully_restored_cases'], 1)
                self.assertEqual(result['changed_input_count'], 1)
                self.assertFalse(result['old_quality_decisions_transferred'])
                self.assertFalse(result['new_quality_review_completed'])
                repaired['messages'][-1]['content'] = '不能改写原始目标'
                with self.assertRaisesRegex(TopicContractError, 'changed a target'):
                    report_context_repair(before=before, after=after, audit=audit,
                                          output_root=root / 'reports', controlled_root=root)


if __name__ == '__main__':
    unittest.main()
