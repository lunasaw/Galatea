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

from wechat_persona._common import digest
from wechat_persona.topic_axes_review import (
    calibration_summary, decode_response, fixture_candidate, load_config, load_fixtures,
    private_summary, protocol_binding, require_calibration, run_review, wire_payload,
)
from wechat_persona.topic_candidates import read_json
from wechat_persona.topic_context import TopicContractError
from wechat_persona.topic_review_protocol import validate_axes


class AxesReviewTests(unittest.TestCase):
    def setUp(self):
        self.config = ROOT / 'configs/topic-axes-review-v2.yaml'
        self.policy = load_config(self.config)
        self.fixture_path, self.fixtures = load_fixtures(self.config, self.policy)
        self.candidates = [fixture_candidate(f) for f in self.fixtures]
        self.axes = {'relation': 'direct_answer', 'context_status': 'sufficient', 'communicative_value': 'useful',
                     'risk_status': 'clear', 'risk_flags': [], 'confidence': .95,
                     'responds_to_indices': [0], 'required_context_indices': [0]}

    def response(self, labels, name='gpt'):
        content = json.dumps({'results': labels})
        common = {'model': self.policy['judges'][name]['model'], 'usage': {'input_tokens': 30, 'output_tokens': 10}}
        if name == 'gpt':
            return {**common, 'status': 'completed', 'output_text': content}
        return {**common, 'choices': [{'finish_reason': 'stop', 'message': {'content': '```json\n' + content + '\n```'}}]}

    def reference_axes(self, fixture):
        expected = fixture['expected']
        relation = 'direct_answer' if expected['reply_link_correct'] else 'unrelated'
        if expected['status'] == 'uncertain' and not expected['reply_link_correct']:
            relation = 'undetermined'
        anchors = expected['allowed_anchor_sets'][0]
        risk = expected['status'] == 'reject' and expected['reply_link_correct']
        return {**self.axes, 'relation': relation,
                'context_status': 'sufficient' if expected['context_complete'] else 'missing_referent',
                'responds_to_indices': anchors, 'required_context_indices': anchors,
                'risk_status': 'flagged' if risk else 'clear', 'risk_flags': ['unsafe'] if risk else []}

    def decisions(self):
        return [{**validate_axes(candidate, self.reference_axes(fixture)), 'judge': name,
                 'model_requested': judge['model'], 'model_returned': judge['model'], 'requested_family': name}
                for name, judge in self.policy['judges'].items()
                for candidate, fixture in zip(self.candidates, self.fixtures)]

    def test_wire_is_blind_and_has_same_explicit_legend_and_schema(self):
        user_payloads, prompts = [], []
        for name, judge in self.policy['judges'].items():
            wire = wire_payload(self.candidates[:2], judge, self.policy['calibration'])
            serialized = json.dumps(wire)
            for hidden in ('axes-fresh-', 'expected', 'candidate_sha256', 'prior_hard_risks', 'allowed_anchor_sets'):
                self.assertNotIn(hidden, serialized)
            messages = wire['input' if name == 'gpt' else 'messages']
            prompts.append(messages[0]['content'])
            user_payloads.append(messages[1]['content'])
            self.assertIn('self 是对方', prompts[-1])
            self.assertIn('required_context_indices', prompts[-1])
        self.assertEqual(prompts[0], prompts[1])
        self.assertEqual(user_payloads[0], user_payloads[1])

    def test_both_transports_and_partial_invalid_cases(self):
        for name in self.policy['judges']:
            labels = [{'index': 0, **self.axes}, {'index': 1, **self.axes, 'relation': 'invented'}]
            valid, failures = decode_response(self.response(labels, name), self.policy['judges'][name], self.candidates[:2])
            self.assertEqual(len(valid), 1)
            self.assertEqual(valid[0]['sample_id'], self.candidates[0]['sample_id'])
            self.assertEqual(failures, {self.candidates[1]['sample_id']: 'invalid_case_axes'})

    def test_duplicate_known_case_is_not_silently_chosen(self):
        label = {'index': 0, **self.axes}
        valid, failures = decode_response(self.response([label, label, {'index': 1, **self.axes}]),
                                          self.policy['judges']['gpt'], self.candidates[:2])
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0]['sample_id'], self.candidates[1]['sample_id'])
        self.assertEqual(len(failures), 1)

    def test_untrusted_identity_completion_indices_and_nonfinite_fail_closed(self):
        response = self.response([{'index': 0, **self.axes}])
        invalid = [{**response, 'model': 'wrong'}, {**response, 'status': 'incomplete'},
                   self.response([{'index': 99, **self.axes}]),
                   self.response([{'index': 0, **self.axes, 'confidence': float('nan')}])]
        for value in invalid:
            with self.assertRaises(TopicContractError):
                decode_response(value, self.policy['judges']['gpt'], self.candidates[:1])
        for change in ({'confidence': 1.01}, {'responds_to_indices': [0, 0]}, {'responds_to_indices': [-1]}):
            valid, failures = decode_response(self.response([{'index': 0, **self.axes, **change}]),
                                               self.policy['judges']['gpt'], self.candidates[:1])
            self.assertEqual(valid, [])
            self.assertEqual(len(failures), 1)

    def test_gate_has_fixed_denominator_and_zero_false_keep_requirement(self):
        decisions = self.decisions()
        self.assertTrue(calibration_summary(self.fixtures, decisions, self.policy)['protocol_gate_passed'])
        incomplete = calibration_summary(self.fixtures, decisions[:-1], self.policy)
        self.assertFalse(incomplete['protocol_gate_passed'])
        self.assertEqual(incomplete['judges']['claude']['population'], 24)
        # One false keep fails even when agreement is 23/24.
        bad = next(i for i, row in enumerate(decisions) if row['sample_id'] == 'axes-fresh-11')
        replacement = validate_axes(self.candidates[10], self.axes)
        decisions[bad].update(replacement)
        summary = calibration_summary(self.fixtures, decisions, self.policy)
        self.assertEqual(summary['judges']['gpt']['status_agrees'], 23)
        self.assertEqual(summary['judges']['gpt']['false_keep'], 1)
        self.assertFalse(summary['protocol_gate_passed'])

    def test_old_risks_and_distinct_atomic_anchors_prevent_selection(self):
        row = self.candidates[5]
        pair = [{**validate_axes(row, {**self.axes, 'responds_to_indices': [i], 'required_context_indices': [i]}),
                 'judge': name, 'model_requested': judge['model'], 'model_returned': judge['model'], 'requested_family': name}
                for i, (name, judge) in enumerate(self.policy['judges'].items())]
        prior = [{'sample_id': row['sample_id'], 'disposition': 'selected', 'prior_hard_risks': []}]
        summary = private_summary([row], prior, pair, self.policy)
        self.assertEqual(summary['selected_count'], 0)
        self.assertEqual(summary['disposition_counts'], {'reply_anchor_disagreement': 1})
        prior[0]['prior_hard_risks'] = ['third_party']
        summary = private_summary([row], prior, pair, self.policy)
        self.assertEqual(summary['disposition_counts'], {'prior_hard_risk_requires_adjudication': 1})
        self.assertFalse(summary['training_ready'])

    def run_args(self, root):
        return dict(scope='calibration', config_path=self.config, output_root=root / 'out', controlled_root=root,
                    base_url='https://fixture.invalid/openai', auth_file=root / 'auth.json', authorization_reference='synthetic-unit-test')

    def mock_provider(self, req, **kwargs):
        payload = json.loads(req.data)
        name = 'gpt' if 'input' in payload else 'claude'
        messages = payload.get('input', payload.get('messages'))
        cases = json.loads(messages[1]['content'])['cases']
        fixtures = {f['reply']: f for f in self.fixtures}
        labels = [{'index': case['index'], **self.reference_axes(fixtures[case['reply']])} for case in cases]
        return io.BytesIO(json.dumps(self.response(labels, name)).encode())

    def test_plan_is_read_only_and_execute_replay_uses_zero_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = self.run_args(root)
            with patch('wechat_persona.topic_axes_review.urlopen') as call:
                plan = run_review(**args)
                self.assertEqual(plan['planned_requests'], 8)
                self.assertFalse((root / 'out').exists())
                call.assert_not_called()
            # Credentials are a synthetic local fixture, never a real key.
            args['auth_file'].write_text('{"OPENAI_API_KEY":"fixture"}')
            with patch('wechat_persona.topic_axes_review.urlopen', side_effect=self.mock_provider) as call:
                report = run_review(**args, execute=True)
                self.assertTrue(report['protocol_gate_passed'])
                self.assertEqual(call.call_count, 8)
            with patch('wechat_persona.topic_axes_review.urlopen') as call:
                replay = run_review(**args, execute=True)
                self.assertEqual(replay, report)
                call.assert_not_called()
            directory = Path(report['workspace'])
            binding = protocol_binding(self.policy, self.fixture_path, args['base_url'])
            receipt = require_calibration(directory, binding, self.fixtures, self.policy)
            self.assertTrue(receipt['protocol_gate_passed'])
            with self.assertRaises(TopicContractError):
                require_calibration(directory, {**binding, 'endpoint_sha256': 'changed'}, self.fixtures, self.policy)
            path = directory / 'decisions.json'
            path.write_text('{}')
            with self.assertRaises(TopicContractError):
                run_review(**args, execute=True)

    def test_failed_synthetic_gate_blocks_private_inputs_and_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = self.run_args(root)
            args['auth_file'].write_text('{"OPENAI_API_KEY":"fixture"}')

            def fail_all(req, **kwargs):
                payload = json.loads(req.data)
                name = 'gpt' if 'input' in payload else 'claude'
                return io.BytesIO(json.dumps(self.response([], name)).encode())

            with patch('wechat_persona.topic_axes_review.urlopen', side_effect=fail_all):
                report = run_review(**args, execute=True)
            self.assertEqual(report['status'], 'incomplete')
            self.assertFalse(report['protocol_gate_passed'])
            args.update(scope='private_review', calibration=Path(report['workspace']), private_inputs={})
            with patch('wechat_persona.topic_axes_review.load_private_inputs') as load, patch('wechat_persona.topic_axes_review.urlopen') as call:
                with self.assertRaises(TopicContractError):
                    run_review(**args, execute=True)
                load.assert_not_called()
                call.assert_not_called()

    def test_invalid_json_is_preserved_and_reserved_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = self.run_args(root)
            args['auth_file'].write_text('{"OPENAI_API_KEY":"fixture"}')
            with patch('wechat_persona.topic_axes_review.urlopen', side_effect=lambda *a, **k: io.BytesIO(b'not JSON')) as call:
                report = run_review(**args, execute=True)
                self.assertEqual(call.call_count, 8)
            directory = Path(report['workspace'])
            self.assertEqual(report['usage']['requests'], 8)
            self.assertEqual(report['usage']['requests_without_reported_usage'], 8)
            raw = [read_json(path) for path in directory.glob('*.json') if len(path.stem) == 64]
            self.assertEqual(len(raw), 8)
            self.assertTrue(all(r['raw_body_base64'] == 'bm90IEpTT04=' for r in raw))
            with patch('wechat_persona.topic_axes_review.urlopen') as call:
                self.assertEqual(run_review(**args, execute=True), report)
                call.assert_not_called()

    def test_private_repair_only_resends_invalid_cases_and_preserves_draft_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = self.run_args(root)
            args['auth_file'].write_text('{"OPENAI_API_KEY":"fixture"}')
            with patch('wechat_persona.topic_axes_review.urlopen', side_effect=self.mock_provider):
                calibration = run_review(**args, execute=True)
            candidates = copy.deepcopy(self.candidates[:3])
            prior = [{'sample_id': r['sample_id'], 'disposition': 'selected', 'prior_hard_risks': []} for r in candidates]
            calls = []

            def partial_provider(req, **kwargs):
                payload = json.loads(req.data)
                name = 'gpt' if 'input' in payload else 'claude'
                cases = json.loads(payload.get('input', payload.get('messages'))[1]['content'])['cases']
                calls.append((name, [r['reply'] for r in cases]))
                labels = [{'index': r['index'], **self.axes} for r in cases]
                if name == 'gpt' and len(cases) == 3:
                    labels[0]['relation'] = 'invalid'
                    # A valid rejection is final and must not be retried.
                    labels[1].update(relation='unrelated', responds_to_indices=[], required_context_indices=[])
                return io.BytesIO(json.dumps(self.response(labels, name)).encode())

            args.update(scope='private_review', calibration=Path(calibration['workspace']),
                        private_inputs={k: root / k for k in ('before', 'after', 'review', 'audit', 'cross', 'consent', 'cross_policy')})
            with patch('wechat_persona.topic_axes_review.load_private_inputs', return_value=(candidates, prior, {})), \
                 patch('wechat_persona.topic_axes_review.urlopen', side_effect=partial_provider):
                report = run_review(**args, execute=True)
            self.assertEqual(report['status'], 'complete')
            self.assertEqual(report['usage']['requests'], 3)
            self.assertEqual(len(calls), 3)
            first = next(contents for name, contents in calls if name == 'gpt' and len(contents) == 3)
            repair = next(contents for name, contents in calls if name == 'gpt' and len(contents) == 1)
            self.assertEqual(repair, first[:1])
            self.assertEqual(report['selected_count'], 2)
            draft = [json.loads(line) for line in (Path(report['workspace']) / 'train.draft.jsonl').read_text().splitlines()]
            self.assertEqual(draft, [r for r in candidates if r['sample_id'] in report['selected_sample_ids']])
            self.assertEqual(candidates, self.candidates[:3])

    def test_fresh_cases_are_not_the_old_development_cases(self):
        old = json.loads((ROOT / 'configs/fixtures/topic-adjudication-v1.json').read_text())
        old_inputs = {digest({'past': f['past'], 'reply': f['reply']}) for f in old}
        new_inputs = {digest({'past': f['past'], 'reply': f['reply']}) for f in self.fixtures}
        self.assertEqual(len(new_inputs), 24)
        self.assertFalse(old_inputs & new_inputs)


if __name__ == '__main__':
    unittest.main()
