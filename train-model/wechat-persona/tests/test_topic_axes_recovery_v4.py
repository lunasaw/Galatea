from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_topic_axes_review_v4 as fixtures

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_axes_review_v4 import run_review
from wechat_persona.topic_axes_recovery_v4 import load_config, repair_batches, run_recovery
from wechat_persona.topic_candidates import read_json
from wechat_persona.topic_context import TopicContractError


class ResponseRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.AxesV4Tests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)

    def test_repairs_distinguish_absent_responses_from_case_failures(self):
        policy = load_config(ROOT / 'configs/topic-axes-recovery-v4.yaml')
        failed = {('claude', f'whole-{i}'): 'incomplete axes response' for i in range(7)}
        failed.update({('gpt', f'case-{i}'): 'invalid_case_axes' for i in range(3)})
        failed[('gpt', 'identity-drift')] = 'model_identity_mismatch'
        batches = repair_batches(failed, policy)
        self.assertEqual([len(b['ids']) for b in batches], [6, 1, 2, 1])
        requested = [(b['judge'], sid) for b in batches for sid in b['ids']]
        self.assertEqual(len(requested), len(set(requested)))
        self.assertEqual(set(requested), set(failed) - {('gpt', 'identity-drift')})

    def prepare(self, root):
        helper = self.helper
        args = helper.preflight(helper.args(root))
        calibration = run_review(**args, execute=True)
        candidates = [helper.candidates[i] for i in (0, 1, 13)]
        prior = [{'sample_id': r['sample_id'], 'disposition': 'selected', 'prior_hard_risks': []}
                 for r in candidates]
        source = patch('wechat_persona.topic_axes_review_v4.core.load_private_inputs', return_value=(candidates, prior, {}))
        source.start()
        self.addCleanup(source.stop)

        def fault(name, batch, model, labels):
            for fixture, label in zip(batch, labels):
                if name == 'gpt' and fixture['id'] == candidates[0]['sample_id']:
                    label.pop('uncertainties')
            return model, labels

        helper.fault = fault
        private_args = helper.private_args(args, calibration)
        parent = run_review(**private_args, execute=True)
        self.assertEqual(parent['status'], 'incomplete')
        helper.fault = None
        private_args.pop('scope')
        return {**private_args, 'config_path': ROOT / 'configs/topic-axes-recovery-v4.yaml',
                'parent': Path(parent['workspace'])}, parent

    def test_recovery_preserves_valid_and_uncertain_rows_replays_and_checks_parent(self):
        with tempfile.TemporaryDirectory() as tmp, patch('wechat_persona.topic_axes_review_v4.urlopen', side_effect=self.helper.provider):
            args, parent = self.prepare(Path(tmp))
            before = len(self.helper.calls)
            plan = run_recovery(**args)
            self.assertEqual(plan['missing_judgments'], 1)
            self.assertEqual(plan['inherited_judgments'], 5)
            self.assertEqual(len(self.helper.calls), before)
            self.assertFalse(Path(plan['workspace']).exists())
            report = run_recovery(**args, execute=True)
            self.assertEqual(report['status'], 'complete')
            self.assertEqual(report['selected_count'], 2)
            self.assertEqual(report['usage']['requests'], 1)
            self.assertEqual(self.helper.calls[before:], [('gpt', [self.helper.candidates[0]['sample_id']])])
            inherited = read_json(args['parent'] / 'decisions.json')['decisions']
            restored = read_json(Path(report['workspace']) / 'decisions.json')['decisions']
            self.assertTrue(all(row in restored for row in inherited))
            self.assertEqual(read_json(args['parent'] / 'report.json'), parent)
            self.assertEqual(run_recovery(**args, execute=True), report)
            self.assertEqual(len(self.helper.calls), before + 1)
            (args['parent'] / 'report.json').write_text('{}')
            with self.assertRaises(TopicContractError):
                run_recovery(**args, execute=True)
            self.assertEqual(len(self.helper.calls), before + 1)

    def test_recovery_identity_drift_exports_no_draft_and_cannot_chain(self):
        with tempfile.TemporaryDirectory() as tmp, patch('wechat_persona.topic_axes_review_v4.urlopen', side_effect=self.helper.provider):
            args, parent = self.prepare(Path(tmp))
            self.helper.fault = lambda name, batch, model, labels: ('gpt-6-sol', labels)
            report = run_recovery(**args, execute=True)
            self.assertEqual(report['status'], 'incomplete')
            self.assertTrue(report['model_identity_circuit_open'])
            self.assertEqual(report['usage']['requests'], 1)
            self.assertFalse(report['draft_exported'])
            self.assertFalse((Path(report['workspace']) / 'train.draft.jsonl').exists())
            before = len(self.helper.calls)
            with self.assertRaises(TopicContractError):
                run_recovery(**{**args, 'parent': Path(report['workspace'])}, execute=True)
            self.assertEqual(len(self.helper.calls), before)
            self.assertEqual(read_json(args['parent'] / 'report.json'), parent)


if __name__ == '__main__':
    unittest.main()
