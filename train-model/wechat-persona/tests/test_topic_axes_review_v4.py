from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from wechat_persona._common import digest, file_digest
from wechat_persona import topic_axes_support_v4 as core
from wechat_persona.topic_axes_review_v4 import (
    METHOD, PROMPT, PROBES, Requests, binding_for, load_config, require_preflight,
    run_review, validate_v4_decisions, wire_payload,
)
from wechat_persona.topic_candidates import read_json
from wechat_persona.topic_context import TopicContractError
from wechat_persona.topic_review_protocol_v4 import validate_axes


class AxesV4Tests(unittest.TestCase):
    def setUp(self):
        self.config = ROOT / 'configs/topic-axes-review-v4.yaml'
        self.policy = load_config(self.config)
        self.fixture_path, self.fixtures = core.load_fixtures(self.config, self.policy)
        self.candidates = [core.fixture_candidate(f) for f in self.fixtures]
        self.calls = []
        audit = patch('wechat_persona.topic_axes_review_v4.require_confidence_audit',
                      side_effect=lambda directory, policy: {'manifest_sha256':
                          __import__('hashlib').sha256((directory / 'manifest.json').read_bytes()).hexdigest(),
                          'purpose': 'hypothesis_basis_not_validation'})
        audit.start()
        self.addCleanup(audit.stop)
        self.fault = None
        self.axes = {'relation': 'direct_answer', 'context_status': 'sufficient', 'communicative_value': 'useful',
                     'risk_status': 'clear', 'risk_flags': [], 'confidence': .95,
                     'responds_to_indices': [0], 'required_context_indices': [0], 'uncertainties': []}

    def reference_axes(self, fixture):
        expected = fixture.get('expected')
        if not expected:
            return dict(self.axes)
        linked = expected['reply_link_correct']
        risk = expected['status'] == 'reject' and linked
        anchors = expected['allowed_anchor_sets'][0]
        return {**self.axes, 'relation': 'direct_answer' if linked else 'unrelated',
                'context_status': 'sufficient' if expected['context_complete'] else 'missing_referent',
                'responds_to_indices': anchors, 'required_context_indices': anchors,
                'risk_status': 'flagged' if risk else 'clear', 'risk_flags': ['unsafe'] if risk else [],
                'uncertainties': [] if expected['context_complete'] else [{
                    'axis': 'context', 'issue': 'missing_referent', 'source_indices': anchors,
                    'reply_quote': fixture['reply']}]}

    def provider(self, request, **kwargs):
        if request.data is None:
            self.calls.append(('catalog', []))
            return io.BytesIO(json.dumps({'data': [{'id': j['model']} for j in self.policy['judges'].values()]}).encode())
        payload = json.loads(request.data)
        name = 'gpt' if 'input' in payload else 'claude'
        cases = json.loads(payload.get('input', payload.get('messages'))[1]['content'])['cases']
        by_input = {digest({'past': f['past'], 'reply': f['reply']}): f for f in [*self.fixtures, *PROBES]}
        fixtures = [by_input[digest({'past': [[m[1], m[4]] for m in case['past_messages']], 'reply': case['reply']})] for case in cases]
        self.calls.append((name, [f['id'] for f in fixtures]))
        labels = [{'index': i, **self.reference_axes(f)} for i, f in enumerate(fixtures)]
        model = self.policy['judges'][name]['model']
        if self.fault:
            model, labels = self.fault(name, fixtures, model, labels)
        content = json.dumps({'results': labels})
        response = {'model': model, 'usage': {'input_tokens': 100, 'output_tokens': 50}}
        if name == 'gpt':
            response.update(status='completed', output_text=content)
        else:
            response['choices'] = [{'finish_reason': 'stop', 'message': {'content': content}}]
        return io.BytesIO(json.dumps(response).encode())

    def args(self, root):
        auth = root / 'auth.json'
        auth.write_text('{"OPENAI_API_KEY":"fixture"}')
        audit = root / 'audit-basis'
        audit.mkdir()
        (audit / 'manifest.json').write_text('{}')
        return dict(scope='preflight', config_path=self.config, output_root=root / 'out', controlled_root=root,
                    confidence_audit=audit, base_url='https://fixture.invalid/openai', auth_file=auth, authorization_reference='unit-test-v4')

    def preflight(self, args):
        report = run_review(**args, execute=True)
        self.assertTrue(report['route_gate_passed'])
        return {**args, 'scope': 'calibration', 'preflight': Path(report['workspace'])}

    def private_args(self, args, calibration):
        root = args['controlled_root']
        return {**args, 'scope': 'private_review', 'calibration': Path(calibration['workspace']),
                'private_inputs': {k: root / k for k in ('before', 'after', 'review', 'audit', 'cross', 'consent', 'cross_policy')}}

    def test_plan_does_not_call_gateway_or_write_output(self):
        with tempfile.TemporaryDirectory() as tmp, patch('wechat_persona.topic_axes_review_v4.urlopen') as call:
            args = self.args(Path(tmp))
            plan = run_review(**args)
            self.assertEqual(plan['planned_requests'], 4)
            self.assertFalse(args['output_root'].exists())
            call.assert_not_called()

    def test_fresh_fixtures_and_blind_identical_semantic_prompts(self):
        old = []
        for name in ('topic-adjudication-v1.json', 'topic-axes-validation-v2.json', 'topic-axes-validation-v3.json'):
            old.extend(json.loads((ROOT / 'configs/fixtures' / name).read_text()))
        old_inputs = {digest({'past': f['past'], 'reply': f['reply']}) for f in old}
        self.assertFalse(old_inputs & {digest({'past': f['past'], 'reply': f['reply']}) for f in self.fixtures})
        prompts = []
        for name, judge in self.policy['judges'].items():
            wire = wire_payload(self.candidates[:2], judge, self.policy['calibration'])
            messages = wire['input' if name == 'gpt' else 'messages']
            prompts.append(messages[0]['content'])
            for marker in ('axes-fresh-', 'expected', 'allowed_anchor_sets', 'prior_hard_risks'):
                self.assertNotIn(marker, json.dumps(wire))
        self.assertEqual(prompts[0], prompts[1])
        self.assertIn('不是信息量', prompts[0])
        self.assertIn('不得为了任何门槛虚报分数', prompts[0])
        self.assertIn('uncertainties', prompts[0])

    def test_shadow_confidence_never_overwrites_original_score(self):
        row = self.candidates[0]
        decision = validate_axes(row, {**self.axes, 'confidence': .72})
        self.assertEqual(decision['status'], 'keep')
        self.assertEqual(decision['shadow_v2_status'], 'uncertain')
        self.assertEqual(decision['axes']['confidence'], .72)
        risk = validate_axes(row, {**self.axes, 'risk_status': 'flagged', 'risk_flags': ['control_or_abuse']})
        self.assertEqual(risk['status'], 'reject')
        self.assertTrue(risk['reply_link_correct'])

    def test_preflight_and_calibration_roundtrip_is_immutable_and_binds_exact_model(self):
        with tempfile.TemporaryDirectory() as tmp, patch('wechat_persona.topic_axes_review_v4.urlopen', side_effect=self.provider):
            args = self.preflight(self.args(Path(tmp)))
            self.assertEqual(len(self.calls), 5)  # One catalog read plus four bounded probes.
            report = run_review(**args, execute=True)
            self.assertTrue(report['protocol_gate_passed'])
            self.assertEqual(report['usage']['requests'], 8)
            self.assertEqual(len(self.calls), 13)
            self.assertEqual(run_review(**args, execute=True), report)
            self.assertEqual(len(self.calls), 13)
            directory = args['preflight']
            binding = binding_for(self.policy, self.fixture_path, args['base_url'])
            receipt = require_preflight(directory, binding, self.policy)
            self.assertEqual(receipt['bound_returned_models'], {'gpt': 'gpt-5.6-sol', 'claude': 'claude-sonnet-4-6'})
            with self.assertRaises(TopicContractError):
                require_preflight(directory, {**binding, 'prompt_sha256': 'changed'}, self.policy)

    def test_model_drift_opens_circuit_and_blocks_calibration(self):
        def fault(name, fixtures, model, labels):
            # Either worker can finish first; both routes drift in this circuit fixture.
            return ('unexpected-model-' + name), labels
        self.fault = fault
        with tempfile.TemporaryDirectory() as tmp, patch('wechat_persona.topic_axes_review_v4.urlopen', side_effect=self.provider):
            args = self.args(Path(tmp))
            report = run_review(**args, execute=True)
            self.assertFalse(report['route_gate_passed'])
            self.assertTrue(report['model_identity_circuit_open'])
            self.assertLessEqual(report['usage']['requests'], 2)
            before = len(self.calls)
            with self.assertRaises(TopicContractError):
                run_review(**{**args, 'scope': 'calibration', 'preflight': Path(report['workspace'])}, execute=True)
            self.assertEqual(len(self.calls), before)
            self.assertEqual(run_review(**args, execute=True), report)
            self.assertEqual(len(self.calls), before)

    def test_one_false_keep_blocks_private_loading_despite_complete_responses(self):
        with tempfile.TemporaryDirectory() as tmp, patch('wechat_persona.topic_axes_review_v4.urlopen', side_effect=self.provider):
            args = self.preflight(self.args(Path(tmp)))

            def fault(name, fixtures, model, labels):
                for f, label in zip(fixtures, labels):
                    if name == 'gpt' and f['id'] == 'axes-fresh-v4-14':
                        label['context_status'] = 'sufficient'
                        label['uncertainties'] = []
                return model, labels

            self.fault = fault
            report = run_review(**args, execute=True)
            self.assertEqual(report['status'], 'complete')
            self.assertFalse(report['protocol_gate_passed'])
            self.assertEqual(report['judges']['gpt']['status_agrees'], 23)
            before = len(self.calls)
            with patch('wechat_persona.topic_axes_review_v4.core.load_private_inputs') as load:
                with self.assertRaisesRegex(TopicContractError, 'synthetic gate failed'):
                    run_review(**self.private_args(args, report), execute=True)
                load.assert_not_called()
            self.assertEqual(len(self.calls), before)

    def test_calibration_model_must_equal_probed_identity_not_just_prefix(self):
        with tempfile.TemporaryDirectory() as tmp, patch('wechat_persona.topic_axes_review_v4.urlopen', side_effect=self.provider):
            args = self.preflight(self.args(Path(tmp)))
            self.fault = lambda name, fixtures, model, labels: (model + '-other', labels)
            report = run_review(**args, execute=True)
            self.assertFalse(report['protocol_gate_passed'])
            self.assertTrue(report['model_identity_circuit_open'])
            self.assertLessEqual(report['usage']['requests'], 2)

    def test_private_draft_preserves_rows_and_prior_risks_with_full_coverage(self):
        with tempfile.TemporaryDirectory() as tmp, patch('wechat_persona.topic_axes_review_v4.urlopen', side_effect=self.provider):
            args = self.preflight(self.args(Path(tmp)))
            report = run_review(**args, execute=True)
            candidates = copy.deepcopy(self.candidates[:3])
            prior = [{'sample_id': r['sample_id'], 'disposition': 'selected', 'prior_hard_risks': ['privacy'] if i == 0 else []}
                     for i, r in enumerate(candidates)]
            with patch('wechat_persona.topic_axes_review_v4.core.load_private_inputs', return_value=(candidates, prior, {})):
                report = run_review(**self.private_args(args, report), execute=True)
            self.assertTrue(report['draft_exported'])
            self.assertEqual(report['selected_count'], 2)
            self.assertEqual(report['disposition_counts']['prior_hard_risk_requires_adjudication'], 1)
            draft = [json.loads(line) for line in (Path(report['workspace']) / 'train.draft.jsonl').read_text().splitlines()]
            self.assertEqual(draft, candidates[1:])
            self.assertEqual(candidates, self.candidates[:3])
            self.assertFalse(report['formal_training_eligible'])

    def test_identity_failure_in_private_review_exports_no_partial_draft(self):
        with tempfile.TemporaryDirectory() as tmp, patch('wechat_persona.topic_axes_review_v4.urlopen', side_effect=self.provider):
            args = self.preflight(self.args(Path(tmp)))
            calibration = run_review(**args, execute=True)
            candidates = self.candidates[:3]
            prior = [{'sample_id': r['sample_id'], 'disposition': 'selected', 'prior_hard_risks': []} for r in candidates]
            self.fault = lambda name, fixtures, model, labels: ('gpt-6' if name == 'gpt' else model, labels)
            with patch('wechat_persona.topic_axes_review_v4.core.load_private_inputs', return_value=(candidates, prior, {})):
                report = run_review(**self.private_args(args, calibration), execute=True)
            self.assertEqual(report['status'], 'incomplete')
            self.assertFalse(report['draft_exported'])
            self.assertFalse((Path(report['workspace']) / 'train.draft.jsonl').exists())
            self.assertLessEqual(report['usage']['requests'], 2)

    def test_raw_cache_cannot_be_detached_from_budget_reservation(self):
        with tempfile.TemporaryDirectory() as tmp, patch('wechat_persona.topic_axes_review_v4.urlopen', side_effect=self.provider):
            args = self.args(Path(tmp))
            workspace = Path(tmp) / 'raw'
            workspace.mkdir()
            identity = {'budget': self.policy['preflight']}
            requests = Requests(workspace, identity, self.policy, args['base_url'], args['auth_file'], None)
            row = core.fixture_candidate(PROBES[0])
            batch = {'judge': 'gpt', 'ids': [row['sample_id']], 'phase': 'first'}
            valid, failed = requests.call(batch, [row])
            self.assertEqual(len(valid), 1)
            self.assertFalse(failed)
            ledger = read_json(workspace / 'budget.json')
            ledger['attempts'][0]['request_digest'] = '0' * 64
            (workspace / 'budget.json').write_text(json.dumps(ledger))
            with self.assertRaisesRegex(TopicContractError, 'budget reservation changed'):
                requests.call(batch, [row])

    def test_format_repair_does_not_reask_valid_uncertainty(self):
        with tempfile.TemporaryDirectory() as tmp, patch('wechat_persona.topic_axes_review_v4.urlopen', side_effect=self.provider):
            args = self.preflight(self.args(Path(tmp)))
            calibration = run_review(**args, execute=True)
            candidates = [self.candidates[0], self.candidates[13]]
            prior = [{'sample_id': r['sample_id'], 'disposition': 'selected', 'prior_hard_risks': []}
                     for r in candidates]
            before = len(self.calls)
            corrupted = False

            def fault(name, fixtures, model, labels):
                nonlocal corrupted
                for fixture, label in zip(fixtures, labels):
                    if name == 'gpt' and fixture['id'] == candidates[0]['sample_id'] and not corrupted:
                        label.pop('uncertainties')
                        corrupted = True
                return model, labels

            self.fault = fault
            with patch('wechat_persona.topic_axes_review_v4.core.load_private_inputs', return_value=(candidates, prior, {})):
                report = run_review(**self.private_args(args, calibration), execute=True)
            self.assertTrue(report['draft_exported'])
            self.assertEqual(report['selected_count'], 1)
            self.assertEqual(report['usage']['requests'], 3)
            calls = self.calls[before:]
            for judge in ('gpt', 'claude'):
                self.assertEqual(sum(candidates[1]['sample_id'] in ids for name, ids in calls if name == judge), 1)


class AuditBasisTests(unittest.TestCase):
    def audit(self, directory):
        report = {'old_gates_changed': False, 'existing_labels_changed': False,
                  'probability_calibration_established': False}
        (directory / 'identity.json').write_text('{}')
        (directory / 'report.json').write_text(json.dumps(report))
        manifest = {'output_digests': {name: file_digest(directory / name)
                                       for name in ('identity.json', 'report.json')}}
        manifest['manifest_sha256'] = digest(manifest)
        (directory / 'manifest.json').write_text(json.dumps(manifest))
        return {'basis_confidence_audit_manifest_sha256': file_digest(directory / 'manifest.json')}

    def test_audit_requires_exact_manifest_and_unchanged_artifacts(self):
        from wechat_persona.topic_axes_review_v4 import require_confidence_audit
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            policy = self.audit(directory)
            self.assertEqual(require_confidence_audit(directory, policy)['purpose'], 'hypothesis_basis_not_validation')
            with self.assertRaises(TopicContractError):
                require_confidence_audit(directory, {**policy, 'basis_confidence_audit_manifest_sha256': '0' * 64})
            (directory / 'report.json').write_text('{}')
            with self.assertRaises(TopicContractError):
                require_confidence_audit(directory, policy)


if __name__ == '__main__':
    unittest.main()
