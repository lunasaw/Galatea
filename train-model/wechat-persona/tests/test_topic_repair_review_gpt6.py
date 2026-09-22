from __future__ import annotations

import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from wechat_persona import topic_axes_review_gpt6 as axes
from wechat_persona import topic_repair_review_gpt6 as review
from wechat_persona._common import digest, file_digest
from wechat_persona.fact_review_server import _atomic_json
from wechat_persona.topic_context import TopicContractError
import test_topic_train_repair as fixtures


class GPT6RepairReviewTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.RepairReviewQueueTests()
        self.candidate, _, _ = fixture.setup_case()
        self.axes = {'relation': 'direct_answer', 'context_status': 'sufficient',
                     'communicative_value': 'useful', 'risk_status': 'clear',
                     'risk_flags': [], 'confidence': .8, 'responds_to_indices': [0],
                     'required_context_indices': [0], 'uncertainties': []}
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.preflights = []
        for sequence in range(1, 4):
            path = self.root / f'preflight-{sequence}'
            path.mkdir()
            _atomic_json(path / 'identity.json', {
                'authorization_reference': 'test-gpt6', 'route_probe_sequence': sequence})
            _atomic_json(path / 'manifest.json', {'sequence': sequence})
            self.preflights.append(path)
        self.calibration = self.root / 'calibration'
        self.calibration.mkdir()
        _atomic_json(self.calibration / 'identity.json', {
            'authorization_reference': 'test-gpt6'})
        _atomic_json(self.calibration / 'manifest.json', {'calibration': True})
        self.config = review.load_config(ROOT / 'configs/topic-repair-review-gpt6-v1.yaml')
        self.config['workers'] = 1
        self.dispositions = [{
            'parent_sample_id': self.candidate['parent_sample_id'],
            'candidate_sha256': self.candidate['candidate_sha256'],
            'parent_candidate_sha256': self.candidate['parent_candidate_sha256'],
            'prior_hard_risks': [], 'previous_disposition': 'selected',
            'disposition': 'ready_for_fresh_machine_review', **review.GOVERNANCE}]
        self.route = {'bound_returned_models': {
            'gpt': 'gpt-6-sol', 'claude': 'claude-sonnet-4-6'}}
        self.calls = []
        self.wrong_model = False
        self.args = dict(queue=self.root / 'queue', repair=self.root / 'repair',
            consent=self.root / 'consent.json',
            config_path=ROOT / 'configs/topic-repair-review-gpt6-v1.yaml',
            preflights=self.preflights, calibration=self.calibration,
            output_root=self.root / 'output', controlled_root=self.root,
            base_url='https://unit.invalid', auth_file=self.root / 'auth.json',
            authorization_reference='test-gpt6')
        patches = (
            patch.object(review, 'load_config', return_value=self.config),
            patch.object(review.legacy, 'load_sources', return_value=(
                [self.candidate], self.dispositions, {})),
            patch.object(review.base, 'binding_for', return_value={'source_sha256': {}}),
            patch.object(review.base, 'require_preflight', side_effect=self.require_preflight),
            patch.object(review.base, 'require_calibration', return_value={
                'manifest_sha256': file_digest(self.calibration / 'manifest.json'),
                'protocol_gate_passed': True,
                'kind': 'synthetic_protocol_readiness_only'}),
            patch.object(review, 'capture_http', side_effect=self.provider),
        )
        for mocked in patches:
            mocked.start()
            self.addCleanup(mocked.stop)

    def require_preflight(self, path, binding, policy):
        sequence = int(path.name.rsplit('-', 1)[1])
        return {'manifest_sha256': file_digest(path / 'manifest.json'),
                **self.route, 'route_gate_passed': True,
                'route_probe_sequence': sequence}

    def provider(self, payload, **kwargs):
        requested = payload['model']
        self.calls.append(requested)
        returned = 'gpt-5.6-sol' if self.wrong_model and requested == 'gpt-6-sol' else requested
        result = {'model': returned, 'usage': {'input_tokens': 100, 'output_tokens': 50}}
        body = json.dumps({'results': [{**self.axes, 'index': 0}]}, ensure_ascii=False)
        if 'input' in payload:
            result.update(status='completed', output_text=body)
        else:
            result['choices'] = [{'finish_reason': 'stop', 'message': {'content': body}}]
        encoded = base64.b64encode(json.dumps(result).encode()).decode()
        return {'http_status': 200, 'error_type': None, 'raw_body_base64': encoded,
                'raw_body_sha256': digest(encoded), 'body_truncated': False,
                'body_read_failed': False}

    def test_from_scratch_review_replays_without_network(self):
        plan = review.run_review(**self.args)
        self.assertEqual(plan['inherited_judgments'], 0)
        report = review.run_review(**self.args, execute=True)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['reviewed'], 2)
        self.assertFalse(report['old_model_judgments_inherited'])
        workspace = Path(report['workspace'])
        with patch.object(review, 'capture_http', side_effect=AssertionError('network')):
            self.assertEqual(review.run_review(**self.args, execute=True), report)
        self.assertEqual(file_digest(workspace / 'manifest.json'),
                         file_digest(workspace / 'manifest.json'))

    def test_exact_model_drift_persistently_stops(self):
        self.wrong_model = True
        report = review.run_review(**self.args, execute=True)
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(report['circuit_reason'], 'model_identity_mismatch')
        self.assertLess(len(self.calls), 3)

    def test_three_ordered_distinct_preflights_required(self):
        with self.assertRaisesRegex(TopicContractError, 'distinct'):
            review.run_review(**{**self.args, 'preflights': self.preflights[:2]})
        with self.assertRaisesRegex(TopicContractError, 'stable sequence'):
            review.run_review(**{**self.args,
                'preflights': [self.preflights[1], self.preflights[0], self.preflights[2]]})


class GPT6AxesConfigTests(unittest.TestCase):
    def test_exact_model_and_probe_sequence_are_frozen(self):
        policy = axes.load_config(ROOT / 'configs/topic-axes-review-v4-gpt6.yaml')
        self.assertEqual(policy['judges']['gpt']['model'], 'gpt-6-sol')
        self.assertEqual(policy['judges']['gpt']['returned_model_prefix'], 'gpt-6-sol')
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / 'changed.yaml'
            text = (ROOT / 'configs/topic-axes-review-v4-gpt6.yaml').read_text()
            changed.write_text(text.replace('gpt-6-sol', 'gpt-6'), encoding='utf-8')
            with self.assertRaises(TopicContractError):
                axes.load_config(changed)


if __name__ == '__main__':
    unittest.main()
