from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_topic_axes_review_v4 as fixtures

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona import topic_axes_singleton_v4 as singleton
from wechat_persona.topic_axes_review_v4 import run_review
from wechat_persona.topic_axes_recovery_v4 import run_recovery
from wechat_persona.topic_candidates import read_json
from wechat_persona._common import file_digest
from wechat_persona.topic_context import TopicContractError


class SingletonRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.AxesV4Tests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.empty = False
        self.once = False

    def provider(self, request, **kwargs):
        response = self.helper.provider(request, **kwargs)
        result = json.loads(response.getvalue())
        if self.empty and 'choices' in result:
            result['choices'] = [{'finish_reason': 'length', 'message': {'role': 'assistant'}}]
        if self.once and 'choices' in result:
            content = result['choices'][0]['message'].get('content', '')
            if 'empty_once' in content:
                self.once = False
                result['choices'] = [{'finish_reason': 'length', 'message': {'role': 'assistant'}}]
        return io.BytesIO(json.dumps(result).encode())

    def prepare(self, directory):
        helper = self.helper
        args = helper.preflight(helper.args(directory))
        calibration = run_review(**args, execute=True)
        candidates = [helper.candidates[i] for i in (0, 1, 13)]
        prior = [{'sample_id': r['sample_id'], 'disposition': 'selected', 'prior_hard_risks': []}
                 for r in candidates]
        source = patch('wechat_persona.topic_axes_review_v4.core.load_private_inputs', return_value=(candidates, prior, {}))
        source.start()
        self.addCleanup(source.stop)

        def invalid(name, batch, model, labels):
            for fixture, label in zip(batch, labels):
                if name == 'claude' and fixture['id'] in {r['sample_id'] for r in candidates[:2]}:
                    label.pop('uncertainties')
            return model, labels

        helper.fault = invalid
        private_args = helper.private_args(args, calibration)
        original = run_review(**private_args, execute=True)
        self.assertEqual(original['reviewed'], 4)
        helper.fault, self.empty = None, True
        private_args.pop('scope')
        parent_args = {**private_args, 'config_path': ROOT / 'configs/topic-axes-recovery-v4.yaml',
                       'parent': Path(original['workspace'])}
        parent = run_recovery(**parent_args, execute=True)
        self.empty = False
        self.assertEqual(parent['response_failure_counts'], {'incomplete axes response': 2})
        config_path = ROOT / 'configs/topic-axes-singleton-v4.yaml'
        policy = singleton.load_config(config_path)
        policy.update(parent_manifest_sha256=file_digest(Path(parent['workspace']) / 'manifest.json'),
                      inherited_judgments=4, missing_judgments=2)
        patched = patch('wechat_persona.topic_axes_singleton_v4.load_config', return_value=policy)
        patched.start()
        self.addCleanup(patched.stop)
        return {**parent_args, 'config_path': config_path, 'parent': Path(parent['workspace']),
                'authorization_reference': 'unit-test-new-singleton-authority'}, candidates

    def test_recovery_only_reasks_empty_response_preserves_valid_reject_and_uncertain(self):
        with tempfile.TemporaryDirectory() as tmp, patch('wechat_persona.topic_axes_review_v4.urlopen', side_effect=self.provider):
            args, candidates = self.prepare(Path(tmp))
            before = len(self.helper.calls)
            plan = singleton.run_recovery(**args)
            self.assertEqual(plan['missing_judgments'], 2)
            self.assertEqual(len(self.helper.calls), before)
            self.assertFalse(Path(plan['workspace']).exists())
            self.once = True

            def fault(name, batch, model, labels):
                for fixture, label in zip(batch, labels):
                    if fixture['id'] == candidates[0]['sample_id']:
                        label['communicative_value'] = 'low_signal'
                    elif self.once:
                        label['empty_once'] = True
                return model, labels

            self.helper.fault = fault
            report = singleton.run_recovery(**args, execute=True)
            self.assertTrue(report['draft_exported'])
            self.assertEqual(report['usage']['requests'], 3)
            self.assertEqual(report['selected_count'], 1)
            calls = self.helper.calls[before:]
            self.assertTrue(all(name == 'claude' and len(ids) == 1 for name, ids in calls))
            self.assertEqual(sum(candidates[0]['sample_id'] in ids for _, ids in calls), 1)
            inherited = read_json(args['parent'] / 'decisions.json')['decisions']
            restored = read_json(Path(report['workspace']) / 'decisions.json')['decisions']
            self.assertTrue(all(r in restored for r in inherited))
            self.assertEqual(singleton.run_recovery(**args, execute=True), report)
            self.assertEqual(len(self.helper.calls), before + 3)
            self.assertFalse((args['parent'] / 'train.draft.jsonl').exists())

    def test_no_new_authority_tampered_parent_or_recursive_budget_reset(self):
        with tempfile.TemporaryDirectory() as tmp, patch('wechat_persona.topic_axes_review_v4.urlopen', side_effect=self.provider):
            args, _ = self.prepare(Path(tmp))
            before = len(self.helper.calls)
            old_auth = read_json(args['parent'] / 'identity.json')['authorization_reference']
            with self.assertRaises(TopicContractError):
                singleton.run_recovery(**{**args, 'authorization_reference': old_auth}, execute=True)
            report = singleton.run_recovery(**args, execute=True)
            with self.assertRaises(TopicContractError):
                singleton.run_recovery(**{**args, 'parent': Path(report['workspace'])}, execute=True)
            (args['parent'] / 'cases.json').write_text('{}')
            with self.assertRaises(TopicContractError):
                singleton.run_recovery(**args, execute=True)
            self.assertEqual(len(self.helper.calls), before + 2)

    def test_identity_drift_fails_closed_without_repair_or_draft(self):
        with tempfile.TemporaryDirectory() as tmp, patch('wechat_persona.topic_axes_review_v4.urlopen', side_effect=self.provider):
            args, _ = self.prepare(Path(tmp))
            self.helper.fault = lambda name, batch, model, labels: ('claude-other', labels)
            report = singleton.run_recovery(**args, execute=True)
            self.assertTrue(report['model_identity_circuit_open'])
            self.assertFalse(report['draft_exported'])
            self.assertLessEqual(report['usage']['requests'], 2)
            self.assertFalse((Path(report['workspace']) / 'train.draft.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
