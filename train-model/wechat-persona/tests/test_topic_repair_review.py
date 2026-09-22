from __future__ import annotations

import base64
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from wechat_persona._common import digest, file_digest
from wechat_persona.fact_review_server import _atomic_json
from wechat_persona.topic_candidates import read_json
from wechat_persona.topic_context import TopicContractError
from wechat_persona import topic_repair_review as review
from wechat_persona.topic_repair_protocol import validate_axes
import test_topic_train_repair as fixtures


class RepairProtocolTests(unittest.TestCase):
    def setUp(self):
        self.row, _, _ = fixtures.RepairReviewQueueTests().setup_case()
        self.axes = {'relation': 'direct_answer', 'context_status': 'sufficient', 'communicative_value': 'useful',
                     'risk_status': 'clear', 'risk_flags': [], 'confidence': .8, 'responds_to_indices': [0],
                     'required_context_indices': [0], 'uncertainties': []}

    def test_new_digest_bound_without_old_schema_or_labels(self):
        decision = validate_axes(self.row, self.axes)
        self.assertEqual(decision['candidate_sha256'], self.row['candidate_sha256'])
        self.assertEqual(decision['split'], 'train')
        self.assertEqual(decision['status'], 'keep')
        self.assertFalse(decision['formal_training_eligible'])

    def test_invalid_anchors_and_fabricated_witnesses_are_rejected(self):
        for change in ({'responds_to_indices': [99]}, {'required_context_indices': []},
                       {'context_status': 'missing_external'}, {'risk_status': 'flagged'}):
            with self.subTest(change=change), self.assertRaises(TopicContractError):
                validate_axes(self.row, {**self.axes, **change})
        pending = {**self.axes, 'context_status': 'missing_external', 'uncertainties': [
            {'axis': 'context', 'issue': 'missing_external', 'source_indices': [0], 'reply_quote': '编造'}]}
        with self.assertRaises(TopicContractError):
            validate_axes(self.row, pending)
        pending['uncertainties'][0]['reply_quote'] = self.row['messages'][-1]['content']
        self.assertEqual(validate_axes(self.row, pending)['status'], 'uncertain')

    def test_prior_risks_changed_targets_or_validation_split_cannot_enter(self):
        for field, value in (('prior_hard_risks', ['privacy']), ('split', 'validation'),
                             ('schema_version', 'topic-reply-candidate-v1')):
            row = deepcopy(self.row)
            row[field] = value
            row['candidate_sha256'] = digest({k: v for k, v in row.items() if k != 'candidate_sha256'})
            with self.subTest(field=field), self.assertRaises(TopicContractError):
                validate_axes(row, self.axes)
        row = deepcopy(self.row)
        row['messages'][-1]['content'] = '更改原文'
        row['candidate_sha256'] = digest({k: v for k, v in row.items() if k != 'candidate_sha256'})
        with self.assertRaises(TopicContractError):
            validate_axes(row, self.axes)


class RepairReviewRunnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RepairProtocolTests()
        self.fixture.setUp()
        self.calls = []
        self.fault = None
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        preflight = self.root / 'preflight'
        preflight.mkdir()
        _atomic_json(preflight / 'identity.json', {'authorization_reference': 'test-authority'})
        for name in ('preflight', 'original', 'calibration'):
            path = self.root / name
            path.mkdir(exist_ok=True)
            _atomic_json(path / 'manifest.json', {'test': name})
        self.config = review.load_config(ROOT / 'configs/topic-repair-review-v1.yaml')
        self.config['workers'] = 1
        row = self.fixture.row
        self.candidates = [row]
        self.dispositions = [{'parent_sample_id': row['parent_sample_id'], 'candidate_sha256': row['candidate_sha256'],
            'parent_candidate_sha256': row['parent_candidate_sha256'], 'prior_hard_risks': [],
            'previous_disposition': 'selected', 'disposition': 'ready_for_fresh_machine_review', **review.GOVERNANCE}]
        self.args = dict(queue=self.root / 'queue', repair=self.root / 'repair', consent=self.root / 'consent.json',
            config_path=ROOT / 'configs/topic-repair-review-v1.yaml', preflight=preflight,
            original_preflight=self.root / 'original', calibration=self.root / 'calibration',
            output_root=self.root / 'output', controlled_root=self.root, base_url='https://unit.invalid',
            auth_file=self.root / 'missing-auth.json', authorization_reference='test-authority')
        route = {'bound_returned_models': {'gpt': 'gpt-5.6-sol', 'claude': 'claude-sonnet-4-6'}}
        for name, kwargs in (
            ('load_config', {'return_value': self.config}),
            ('load_sources', {'return_value': (self.candidates, self.dispositions, {})}),
            ('base.require_preflight', {'return_value': route}),
            ('base.require_calibration', {'return_value': {'protocol_gate_passed': True}}),
            ('capture_http', {'side_effect': self.provider})):
            p = patch('wechat_persona.topic_repair_review.' + name, **kwargs)
            p.start()
            self.addCleanup(p.stop)

    def provider(self, payload, **kwargs):
        model = payload['model']
        self.calls.append(model)
        axes = {**self.fixture.axes, 'index': 0}
        if self.fault:
            fault = self.fault(model, axes)
            if fault:
                return fault
        text = json.dumps({'results': [axes]}, ensure_ascii=False)
        result = {'model': model, 'usage': {'input_tokens': 100, 'output_tokens': 50}}
        if 'input' in payload:
            result.update(status='completed', output_text=text)
        else:
            result['choices'] = [{'finish_reason': 'stop', 'message': {'content': text}}]
        body = base64.b64encode(json.dumps(result).encode()).decode()
        return {'http_status': 200, 'error_type': None, 'raw_body_base64': body, 'raw_body_sha256': digest(body),
                'body_truncated': False, 'body_read_failed': False}

    def test_plan_no_write_execute_complete_and_replay_no_auth_or_network(self):
        plan = review.run_review(**self.args)
        self.assertFalse(Path(plan['workspace']).exists())
        self.assertEqual(self.calls, [])
        report = review.run_review(**self.args, execute=True)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['reviewed'], 2)
        self.assertEqual(report['disposition_counts'], {'machine_consensus_keep': 1})
        self.assertFalse(report['p3_accepted'])
        workspace = Path(report['workspace'])
        sha = file_digest(workspace / 'manifest.json')
        with patch('wechat_persona.topic_repair_review.capture_http', side_effect=AssertionError('network')):
            self.assertEqual(review.run_review(**self.args, execute=True), report)
        self.assertEqual(file_digest(workspace / 'manifest.json'), sha)
        self.assertEqual(len(self.calls), 2)
        (workspace / 'decisions.json').write_text('{}')
        with self.assertRaises(TopicContractError):
            review.run_review(**self.args, execute=True)

    def test_invalid_only_repaired_once_valid_reject_not_reasked(self):
        count = Counter()

        def fault(model, axes):
            count[model] += 1
            if model.startswith('claude'):
                axes['communicative_value'] = 'low_signal'
            elif count[model] == 1:
                axes['responds_to_indices'] = [50]

        self.fault = fault
        report = review.run_review(**self.args, execute=True)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['repair_requests'], 1)
        self.assertEqual(self.calls.count('claude-sonnet-4-6'), 1)
        self.assertEqual(report['decision_counts']['claude'], {'reject': 1})

    def test_wrong_model_opens_persistent_circuit_without_retry(self):
        def fault(model, axes):
            result = {'model': 'unexpected', 'usage': {'input_tokens': 100, 'output_tokens': 20}}
            encoded = base64.b64encode(json.dumps(result).encode()).decode()
            return {'http_status': 200, 'error_type': None, 'raw_body_base64': encoded,
                    'raw_body_sha256': digest(encoded)}

        self.fault = fault
        report = review.run_review(**self.args, execute=True)
        self.assertEqual(report['circuit_reason'], 'model_identity_mismatch')
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(review.run_review(**self.args, execute=True), report)

    def test_interrupted_reservation_not_redispatched(self):
        with patch('wechat_persona.topic_repair_review.capture_http', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):
                review.run_review(**self.args, execute=True)
        report = review.run_review(**self.args, execute=True)
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(report['response_failure_counts'], {'interrupted_request_not_redispatched': 1})
        self.assertEqual(len(self.calls), 1)

    def test_usage_above_reservation_stops_dispatch(self):
        def fault(model, axes):
            result = {'model': model, 'usage': {'input_tokens': 1000000, 'output_tokens': 20}}
            encoded = base64.b64encode(json.dumps(result).encode()).decode()
            return {'http_status': 200, 'error_type': None, 'raw_body_base64': encoded,
                    'raw_body_sha256': digest(encoded)}

        self.fault = fault
        report = review.run_review(**self.args, execute=True)
        self.assertEqual(report['circuit_reason'], 'provider_usage_exceeded_reservation')
        self.assertEqual(len(self.calls), 1)


if __name__ == '__main__':
    unittest.main()
