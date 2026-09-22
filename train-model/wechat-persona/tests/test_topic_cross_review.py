from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from wechat_persona._common import digest
from wechat_persona.topic_candidates import load_policy
from wechat_persona.topic_context import TopicContractError
from wechat_persona.topic_cross_review import (
    METHOD, decode_response, input_identity, load_config, make_batches,
    run_cross_review, summarize, validate_cached, wire_payload,
)
from wechat_persona.topic_evidence_audit import SCHEMA, evidence_view, parse_response
import test_topic_candidates as fixtures


class CrossReviewTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.TopicCandidateTests()
        fixture.setUp()
        fixture.config = load_policy(ROOT / 'configs/daily-topic-sft-v2.yaml')
        self.row = fixture.candidate()
        self.view = evidence_view(self.row, self.row['context_messages'], 'selected')
        self.policy = load_config(ROOT / 'configs/topic-cross-review-v1.yaml')
        self.label = {'index': 0, 'status': 'keep', 'reason': 'usable_reply', 'confidence': .95,
                      'reply_link_correct': True, 'context_complete': True,
                      'responds_to_indices': [2], 'required_context_indices': [0, 1, 2]}
        self.evidence = {self.row['sample_id']: self.decision()}
        self.previous = copy.deepcopy(self.evidence)
        self.roster = [{'before_sample_id': self.row['sample_id'], 'after_sample_id': self.row['sample_id'],
                        'before_input_sha256': input_identity(self.row), 'after_input_sha256': input_identity(self.row),
                        'changed': False, 'keep_disagreement': False, 'prior_consensus_keep': True}]

    def decision(self, scope='v3', **changes):
        parsed = parse_response({'output_text': json.dumps({'results': [{**self.label, **changes}]})}, [self.view], .9)[0]
        return {**parsed, 'scope': scope}

    def summary(self, decisions):
        return summarize([self.row], [self.row], self.previous, self.evidence, self.roster, decisions)

    def test_config_rejects_same_family_and_governance_relaxation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'policy.yaml'
            for mutate in ('family', 'formal_training_eligible'):
                policy = copy.deepcopy(self.policy)
                if mutate == 'family':
                    policy['judges']['independent']['family'] = 'gpt'
                else:
                    policy['governance'][mutate] = True
                path.write_text(json.dumps(policy))
                with self.assertRaises(TopicContractError):
                    load_config(path)

    def test_transports_share_schema_and_hide_old_labels(self):
        for judge in self.policy['judges'].values():
            sent = wire_payload([{**self.view, 'old_status': 'reject'}], self.policy, judge)
            messages = sent.get('messages', sent.get('input'))
            cases = json.loads(messages[1]['content'])['cases']
            self.assertEqual(set(cases[0]), {'index', 'past_messages', 'reply'})
            self.assertNotIn(self.row['sample_id'], json.dumps(sent))
            self.assertNotIn('old_status', json.dumps(sent))
            schema = sent['response_format']['json_schema']['schema'] if 'messages' in sent else sent['text']['format']['schema']
            self.assertEqual(schema, SCHEMA)

    def test_returned_model_and_completion_required_for_both_transports(self):
        text = json.dumps({'results': [self.label]})
        for judge in self.policy['judges'].values():
            response = {'model': judge['model'], 'output_text': text, 'status': 'completed',
                        'choices': [{'finish_reason': 'stop', 'message': {'content': text}}],
                        'usage': {'prompt_tokens': 100, 'completion_tokens': 40}}
            decisions, usage = decode_response(response, judge, [self.view], .9)
            self.assertEqual(usage, {'input_tokens': 100, 'output_tokens': 40})
            self.assertEqual(decisions[0]['requested_family'], judge['family'])
            for bad in (None, 'wrong-model'):
                with self.assertRaisesRegex(TopicContractError, 'model identity'):
                    decode_response({**response, 'model': bad}, judge, [self.view], .9)
            response['status'] = 'incomplete'
            response['choices'][0]['finish_reason'] = 'length'
            with self.assertRaisesRegex(TopicContractError, 'incomplete'):
                decode_response(response, judge, [self.view], .9)

    def test_chat_fence_accepted_but_missing_schema_fields_never_invented(self):
        judge = self.policy['judges']['independent']
        for label, valid in (({'results': [self.label]}, True), ({'index': 0, 'keep': True}, False)):
            response = {'model': judge['model'], 'choices': [{'finish_reason': 'stop', 'message': {
                'content': '```json\n' + json.dumps(label) + '\n```'}}]}
            if valid:
                self.assertEqual(decode_response(response, judge, [self.view], .9)[0][0]['status'], 'keep')
            else:
                with self.assertRaises(TopicContractError):
                    decode_response(response, judge, [self.view], .9)

    def test_equivalence_binds_actual_input_atomic_source_and_order(self):
        original = input_identity(self.row)
        for key, field, value in [('messages', 'content', 'different'),
                                  ('context_messages', 'role', 'target'),
                                  ('context_messages', 'content', 'different'),
                                  ('context_messages', 'order', 20),
                                  ('target_messages', 'message_id', 'different')]:
            changed = copy.deepcopy(self.row)
            changed[key][0][field] = value
            self.assertNotEqual(input_identity(changed), original)
        changed = {**self.row, 'sample_id': 'new-policy-id', 'candidate_sha256': 'new-policy-digest'}
        self.assertEqual(input_identity(changed), original)

    def test_missing_review_preserves_denominator(self):
        result = self.summary([])
        self.assertEqual(result['previous_117_consensus_support']['denominator'], 1)
        self.assertEqual(result['previous_117_consensus_support']['count'], 0)
        self.assertEqual(result['selected_count'], 0)

    def test_changed_input_needs_fresh_reference_and_separate_old_review(self):
        self.roster[0]['changed'] = True
        result = self.summary([self.decision()])
        self.assertEqual(result['selected_count'], 0)
        self.assertEqual(result['previous_117_consensus_support']['count'], 0)
        result = self.summary([self.decision(), self.decision('v3_changed'), self.decision('v2_changed')])
        self.assertEqual(result['selected_count'], 1)
        self.assertEqual(result['bindings'][0]['reference_binding'], 'fresh_v3_review')

    def test_old_hard_risk_survives_fresh_keep(self):
        self.previous[self.row['sample_id']].update(status='reject', reason='privacy')
        self.roster[0].update(keep_disagreement=True, prior_consensus_keep=False)
        result = self.summary([self.decision()])
        self.assertIsNone(result['previous_117_consensus_support'])
        self.assertEqual(result['selected_count'], 0)
        self.assertEqual(result['excluded_counts'], {'prior_hard_risk_requires_adjudication': 1})

    def test_both_keep_still_require_shared_reply_anchor(self):
        result = self.summary([self.decision(responds_to_indices=[0])])
        self.assertEqual(result['selected_count'], 0)
        self.assertEqual(result['excluded_counts'], {'reply_anchor_disagreement': 1})

    def test_duplicate_or_mismatched_decision_rejected(self):
        for decisions in ([self.decision(), self.decision()], [{**self.decision(), 'candidate_sha256': 'wrong'}]):
            with self.assertRaises(TopicContractError):
                self.summary(decisions)

    def test_old_new_views_are_in_separate_requests(self):
        self.roster[0]['changed'] = True
        batches = make_batches([self.row], [self.row], self.roster, self.policy)
        self.assertEqual([row['scope'] for row in batches], ['v3', 'v2_changed', 'v3_changed'])
        self.assertEqual([len(row['views']) for row in batches], [1, 1, 1])

    def test_cache_revalidates_evidence_bindings_even_if_digest_recomputed(self):
        judge = self.policy['judges']['independent']
        decision = {**self.decision(), 'method': METHOD, 'model_requested': judge['model'],
                    'model_returned': judge['model'], 'requested_family': judge['family'], 'judge': 'independent'}
        record = {'request_digest': 'test', 'decisions': [decision], 'decisions_sha256': digest([decision]),
                  'model_requested': judge['model'], 'model_returned': judge['model']}
        batch = {'judge': 'independent', 'scope': 'v3', 'views': [self.view]}
        validate_cached(record, batch, judge, 'test')
        decision['responds_to_ids'] = ['fabricated']
        record['decisions_sha256'] = digest([decision])
        with self.assertRaisesRegex(TopicContractError, 'binding'):
            validate_cached(record, batch, judge, 'test')

    def test_resume_adopts_probe_and_retries_only_missing_batch(self):
        self.test_plan_is_readonly_probes_precede_real_data_and_completed_run_replays(simulate_failure=True)

    def test_plan_is_readonly_probes_precede_real_data_and_completed_run_replays(self, simulate_failure=False):
        seen = []
        def respond(request, **kwargs):
            wire = json.loads(request.data)
            cases = json.loads(wire.get('input', wire.get('messages'))[1]['content'])['cases']
            probe = len(cases[0]['past_messages']) == 1
            seen.append(probe)
            labels = [{**self.label, 'index': i, 'responds_to_indices': [0] if probe else [2],
                       'required_context_indices': [0] if probe else [0, 1, 2]} for i, _ in enumerate(cases)]
            text = json.dumps({'results': labels})
            if simulate_failure and seen == [True, False]:
                text = '{}'
            result = {'model': wire['model'], 'output_text': text,
                      'choices': [{'finish_reason': 'stop', 'message': {'content': text}}],
                      'usage': {'input_tokens': 100, 'output_tokens': 40}}
            class Response:
                def __enter__(inner): return inner
                def __exit__(inner, *args): return False
                def read(inner): return json.dumps(result).encode()
            return Response()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {name: root / name for name in ('before', 'after', 'review', 'audit')}
            for path in paths.values():
                path.mkdir()
                (path / 'manifest.json').write_text(json.dumps({'identity': {'endpoint_sha256': digest('https://example.invalid/')}}))
                (path / 'decisions.json').write_text('{}')
                (path / 'source-audit.json').write_text(json.dumps({'consent_file_sha256': 'test'}))
            consent, auth = root / 'consent.json', root / 'auth.json'
            consent.write_text('{}')
            auth.write_text(json.dumps({'OPENAI_API_KEY': 'synthetic-key'}))
            kwargs = dict(**paths, consent=consent, config_path=ROOT / 'configs/topic-cross-review-v1.yaml',
                          output_root=root / 'output', controlled_root=root, base_url='https://example.invalid/',
                          auth_file=auth, authorization_reference='synthetic-test')
            with patch('wechat_persona.topic_cross_review.load_inputs', return_value=(
                    [self.row], [self.row], self.previous, self.evidence, self.roster)), \
                 patch('wechat_persona.topic_cross_review.verify_consent', return_value={'consent_file_sha256': 'test'}), \
                 patch('wechat_persona.topic_cross_review.urlopen', side_effect=respond) as network:
                plan = run_cross_review(**kwargs)
                self.assertFalse(Path(plan['workspace']).exists())
                self.assertEqual(network.call_count, 0)
                first = run_cross_review(**kwargs, execute=True)
                if simulate_failure:
                    self.assertEqual(first['status'], 'incomplete')
                    self.assertFalse((Path(first['workspace']) / 'manifest.json').exists())
                    kwargs['resume_from'] = Path(first['workspace'])
                    plan = run_cross_review(**kwargs)
                    self.assertEqual(plan['planned_requests'], 1)
                    self.assertEqual(plan['adopted_valid_batches'], 1)
                    first = run_cross_review(**kwargs, execute=True)
                second = run_cross_review(**kwargs, execute=True)
                self.assertEqual(first, second)
                self.assertEqual(network.call_count, 3 if simulate_failure else 2)
                self.assertEqual(seen, [True, False, False] if simulate_failure else [True, False])
                self.assertEqual(first['status'], 'complete')
                self.assertFalse(first['training_ready'])
                workspace = Path(first['workspace'])
                self.assertEqual(json.loads((workspace / 'train.draft.jsonl').read_text()), self.row)
                self.assertEqual(workspace.stat().st_mode & 0o777, 0o700)
                self.assertEqual((workspace / 'train.draft.jsonl').stat().st_mode & 0o777, 0o600)
                (workspace / 'report.json').write_text('{}')
                with self.assertRaisesRegex(TopicContractError, 'completed cross-review changed'):
                    run_cross_review(**kwargs, execute=True)
                self.assertEqual(network.call_count, 3 if simulate_failure else 2)


if __name__ == '__main__':
    unittest.main()
