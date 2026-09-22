from __future__ import annotations

from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona import topic_blind_reference as reference
from wechat_persona._common import digest
from wechat_persona.fact_review_server import _atomic_json
from wechat_persona.topic_candidates import read_json
from wechat_persona.topic_context import TopicContractError


def label_for(data):
    missing = data['selected']['reply'] in {'那个时间不行，换一下吧。', '这是向日葵。'}
    return {'relation': 'linked', 'context': 'insufficient' if missing else 'sufficient',
        'value': 'useful', 'risk': 'clear', 'responds_to_indices': [0], 'required_context_indices': [0],
        'missing_evidence_indices': [], 'issues': ['missing_referent'] if missing else [],
        'reply_quote': data['selected']['reply'], 'rationale': '合成说明'}


class BlindReferenceTests(unittest.TestCase):
    def args(self, root):
        auth = root / 'auth.json'
        _atomic_json(auth, {'OPENAI_API_KEY': 'synthetic'})
        return {'scope': 'preflight', 'config_path': ROOT / 'configs/topic-blind-reference-v1.yaml',
                'output_root': root / 'out', 'controlled_root': root, 'base_url': 'https://fixture.invalid',
                'auth_file': auth, 'authorization_reference': 'synthetic-reference'}

    def provider(self, request, **kwargs):
        payload = json.loads(request.data)
        data = json.loads(payload['messages'][1]['content'])
        self.assertEqual(set(data), {'stage', 'selected'} if data['stage'] == 'selected' else {
            'stage', 'selected', 'first_stage', 'expanded', 'expanded_to_selected', 'prefix_complete_start'})
        return io.BytesIO(json.dumps({'model': payload['model'], 'usage': {'prompt_tokens': 500, 'completion_tokens': 100},
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(label_for(data))}}]}).encode())

    def test_blind_views_and_expanded_evidence_cannot_upgrade_input(self):
        case = reference.fixtures()[0]
        case.update(machine_label='reject', confidence=0.1, old_group='secret')
        data = reference.make_input(case, 'selected')
        self.assertNotIn('machine_label', json.dumps(data))
        label = label_for(data)
        expanded = reference.make_input(case, 'expanded', label)
        expanded['expanded_to_selected'].append(None)
        with self.assertRaises(TopicContractError):
            reference.validate_label({**label, 'missing_evidence_indices': [1]}, expanded)
        amended = {**label, 'context': 'insufficient', 'missing_evidence_indices': [1], 'issues': ['missing_referent']}
        self.assertEqual(reference.validate_label(amended, expanded), amended)
        for bad in ({'responds_to_indices': [True]}, {'reply_quote': 'not in reply'},
                    {'responds_to_indices': [88]}, {'relation': 'unknown'}):
            with self.assertRaises(TopicContractError):
                reference.validate_label({**label, **bad}, data)

    def test_two_stage_preflight_and_offline_immutable_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))
            with patch('wechat_persona.topic_validation_completion.urlopen', side_effect=self.provider) as network:
                self.assertEqual(reference.run_reference(**args)['status'], 'planned')
                self.assertEqual(network.call_count, 0)
                report = reference.run_reference(**args, execute=True)
                self.assertTrue(report['route_gate_passed'])
                self.assertEqual(report['completed_references'], 4)
                self.assertEqual(network.call_count, 8)
            with patch('wechat_persona.topic_validation_completion.urlopen', side_effect=AssertionError('network forbidden')):
                self.assertEqual(reference.run_reference(**args, execute=True), report)
            directory = Path(report['workspace'])
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in directory.iterdir()))
            (directory / 'references.json').write_text('{}')
            with self.assertRaises(TopicContractError):
                reference.run_reference(**args, execute=True)

    def test_identity_drift_is_not_repaired_and_halts(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))
            def provider(request, **kwargs):
                result = json.loads(self.provider(request, **kwargs).getvalue())
                result['model'] = 'unexpected'
                return io.BytesIO(json.dumps(result).encode())
            with patch('wechat_persona.topic_validation_completion.urlopen', side_effect=provider) as network:
                report = reference.run_reference(**args, execute=True)
                self.assertEqual(network.call_count, 1)
            self.assertFalse(report['route_gate_passed'])
            self.assertEqual(report['circuit_reason'], 'model_identity_mismatch')

    def test_crash_after_raw_reconciles_usage_before_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))
            with patch('wechat_persona.topic_validation_completion.urlopen', side_effect=self.provider) as network:
                with patch.object(reference.GuardedBudget, 'settle', side_effect=RuntimeError('interruption')):
                    with self.assertRaises(RuntimeError):
                        reference.run_reference(**args, execute=True)
                report = reference.run_reference(**args, execute=True)
                self.assertTrue(report['route_gate_passed'])
                self.assertEqual(network.call_count, 8)
                self.assertEqual(report['usage']['input_tokens'], 4000)

    def test_interrupted_reservation_is_never_resent(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))
            with patch.object(reference, 'capture_http', side_effect=RuntimeError('interrupted before raw')):
                with self.assertRaises(RuntimeError):
                    reference.run_reference(**args, execute=True)
            directory = next(args['output_root'].iterdir())
            interrupted = len(read_json(directory / 'budget.json')['attempts'])
            with patch('wechat_persona.topic_validation_completion.urlopen', side_effect=self.provider) as network:
                report = reference.run_reference(**args, execute=True)
            self.assertEqual(network.call_count, (4 - interrupted) * 2)
            self.assertEqual(report['completed_references'], 4 - interrupted)
            self.assertEqual(report['usage']['requests'], 8 - interrupted)


if __name__ == '__main__':
    unittest.main()
