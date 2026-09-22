import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_confidence_audit import confidence_only, summarize
from wechat_persona.topic_context import TopicContractError


class ConfidenceAuditTests(unittest.TestCase):
    def setUp(self):
        self.row = {'judge': 'claude', 'sample_id': 'synthetic', 'status': 'uncertain',
                    'axes': {'confidence': .88, 'relation': 'acknowledgement', 'context_status': 'sufficient',
                             'communicative_value': 'useful', 'risk_status': 'clear'}}
        self.case = {'judge': 'claude', 'fixture_id': 'synthetic', 'expected_status': 'keep', 'core_agrees': False, 'false_keep': False}

    def test_only_score_blocked_rows_are_counted(self):
        self.assertTrue(confidence_only(self.row))
        for key, value in [('confidence', .9), ('relation', 'undetermined'), ('context_status', 'missing_referent'),
                           ('communicative_value', 'low_signal'), ('risk_status', 'undetermined')]:
            with self.subTest(key=key):
                self.assertFalse(confidence_only({**self.row, 'axes': {**self.row['axes'], key: value}}))

    def test_audit_preserves_status_references_and_missing_denominator(self):
        original = copy.deepcopy(self.row)
        cases = [self.case, {**self.case, 'fixture_id': 'unreviewed'}]
        report = summarize([self.row], cases)['claude']
        self.assertEqual(report['population'], 2)
        self.assertEqual(report['valid'], 1)
        self.assertEqual(report['confidence_only_reference_keep'], 1)
        self.assertEqual(report['score_bins']['below_0_9']['frozen_core_matches'], 0)
        self.assertEqual(self.row, original)

    def test_duplicate_and_unknown_judgments_are_rejected(self):
        for rows in ([self.row, self.row], [{**self.row, 'sample_id': 'unknown'}]):
            with self.assertRaises(TopicContractError):
                summarize(rows, [self.case])


if __name__ == '__main__':
    unittest.main()
