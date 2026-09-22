from __future__ import annotations

from copy import deepcopy
from collections import Counter
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import test_topic_axes_review_v4 as fixtures
from test_topic_candidates import CharacterTokenizer
from wechat_persona import topic_validation_review as review
from wechat_persona import topic_validation_protocol as protocol
from wechat_persona._common import digest, file_digest
from wechat_persona.fact_review_server import _atomic_json
from wechat_persona.topic_candidates import encode_candidate, read_json
from wechat_persona.topic_context import TopicContractError


def validation_fixture(fixture):
    row = review.base.core.fixture_candidate(fixture)
    row.pop('synthetic_fixture')
    row['schema_version'] = review.preparation.CANDIDATE_VERSION
    row['split'] = 'validation'
    for item in row['context_messages'] + row['target_messages']:
        item.update(split='validation', reply_to=None)
    row.update(policy_sha256='0' * 64, review_status='uncertain', human_review_completed=False,
               formal_training_eligible=False)
    row['messages'] = [{'role': 'system', 'content': '合成核验'}, {'role': 'user', 'content': '\n'.join(
        r['role'] + ': ' + r['content'] for r in row['context_messages'])},
        {'role': 'assistant', 'content': fixture['reply']}]
    row['topic'] = {'schema_version': 'topic-segment-v1', 'topic_id': 'fixture', 'topic_category': 'other',
                    'context_ids': row['context_message_ids'], 'prefix_sha256': '0' * 64}
    row['reply_link'] = {'schema_version': 'reply-link-v1', 'target_message_ids': row['target_message_ids'],
        'responds_to_ids': row['context_message_ids'][:1], 'basis': 'prefix_adjacency_hypothesis',
        'used_target_text_for_selection': False, 'target_reference_used_as_input': False}
    row['tokens'] = encode_candidate(row['messages'], CharacterTokenizer(), 4096, 512)
    row['candidate_sha256'] = digest({k: v for k, v in row.items() if k != 'candidate_sha256'})
    return row


class ValidationReviewTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.AxesV4Tests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.candidates = [validation_fixture(f) for f in self.helper.fixtures]
        self.policy = review.preparation.load_policy(ROOT / 'configs/topic-validation-v1.yaml')

    def test_all_24_fixtures_match_frozen_v4_without_changing_validation_scope(self):
        for fixture, candidate in zip(self.helper.fixtures, self.candidates):
            axes = self.helper.reference_axes(fixture)
            previous = review.base.core.validate_axes(review.base.core.fixture_candidate(fixture), axes)
            current = protocol.validate_axes(candidate, axes)
            for key in previous.keys() - {'method', 'candidate_sha256'}:
                self.assertEqual(current[key], previous[key], (fixture['id'], key))
            self.assertEqual(current['split'], 'validation')
            with self.assertRaises(TopicContractError):
                review.base.core.validate_axes(candidate, axes)
            for judge in self.helper.policy['judges'].values():
                wire = review.preparation.validation_payload(candidate, judge, self.policy['prospective_review'])
                old_wire = review.base.wire_payload([review.base.core.fixture_candidate(fixture)], judge,
                                                  self.policy['prospective_review'])
                self.assertEqual(wire, old_wire)

    def test_candidate_axes_witness_and_atomic_anchor_fail_closed(self):
        candidate = self.candidates[0]
        axes = self.helper.reference_axes(self.helper.fixtures[0])
        for mutation in ({'responds_to_indices': [999]}, {'responds_to_indices': [True]},
                         {'risk_status': 'flagged'}, {'context_status': 'missing_referent'},
                         {'relation': 'unrelated'}):
            with self.subTest(mutation=mutation), self.assertRaises(TopicContractError):
                protocol.validate_axes(candidate, {**axes, **mutation})
        witness = {'axis': 'context', 'issue': 'missing_referent', 'source_indices': [0],
                   'reply_quote': candidate['messages'][-1]['content']}
        pending = {**axes, 'context_status': 'missing_referent', 'uncertainties': [witness]}
        self.assertEqual(protocol.validate_axes(candidate, pending)['status'], 'uncertain')
        for mutation in ({'reply_quote': '不属于原回复'}, {'source_indices': [999]}, {'issue': 'missing_media'}):
            with self.assertRaises(TopicContractError):
                protocol.validate_axes(candidate, {**pending, 'uncertainties': [{**witness, **mutation}]})
        for split in ('train', 'test'):
            changed = deepcopy(candidate)
            changed['split'] = split
            changed['candidate_sha256'] = digest({k: v for k, v in changed.items() if k != 'candidate_sha256'})
            with self.assertRaises(TopicContractError):
                protocol.validate_axes(changed, axes)

    def prepare(self, root):
        # Real synthetic route/calibration manifests; mock only the private packet.
        with patch('wechat_persona.topic_axes_review_v4.urlopen', side_effect=self.helper.provider):
            initial_args = self.helper.args(root)
            args = self.helper.preflight(initial_args)
            calibrated = review.base.run_review(**args, execute=True)
            fresh = review.base.run_review(**{**initial_args, 'authorization_reference': 'new-validation-run'}, execute=True)
        packet = root / 'packet'
        packet.mkdir()
        (packet / 'manifest.json').write_text('{}')
        consent = root / 'consent.json'
        consent.write_text('{}')
        population = self.candidates[:3]
        membership = {r['sample_id']: review.preparation.strata_for(r) for r in population}
        patcher = patch.object(review, 'load_packet', return_value=({'identity': {'source_digests': {}}}, population, membership))
        patcher.start()
        self.addCleanup(patcher.stop)
        return dict(packet=packet, consent=consent, preflight=Path(fresh['workspace']),
            original_preflight=args['preflight'], calibration=Path(calibrated['workspace']),
            config_path=ROOT / 'configs/topic-validation-review-v1.yaml', output_root=root / 'review',
            controlled_root=root, base_url=initial_args['base_url'], auth_file=initial_args['auth_file'],
            authorization_reference='new-validation-run'), population

    def test_plan_repair_does_not_reask_valid_reject_or_uncertain_and_replay_is_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            args, population = self.prepare(Path(tmp))
            calls = []
            def provider(request, **kwargs):
                payload = json.loads(request.data)
                judge = 'gpt' if 'input' in payload else 'claude'
                case = json.loads(payload.get('input', payload.get('messages'))[1]['content'])['cases'][0]
                index = next(i for i, r in enumerate(population) if r['messages'][-1]['content'] == case['reply'])
                calls.append((judge, index))
                response = self.helper.provider(request, **kwargs)
                value = json.loads(response.getvalue())
                if judge == 'claude' and index == 0 and calls.count((judge, index)) == 1:
                    value['choices'] = [{'finish_reason': 'length', 'message': {'role': 'assistant'}}]
                return io.BytesIO(json.dumps(value).encode())
            def fault(name, batch, model, labels):
                for fixture, label in zip(batch, labels):
                    if fixture['id'] == population[1]['sample_id']:
                        label['communicative_value'] = 'low_signal'
                    if fixture['id'] == population[2]['sample_id']:
                        label.update(context_status='missing_referent', uncertainties=[{
                            'axis': 'context', 'issue': 'missing_referent',
                            'source_indices': label['required_context_indices'], 'reply_quote': fixture['reply']}])
                return model, labels
            self.helper.fault = fault
            with patch.object(review, 'urlopen', side_effect=provider):
                plan = review.run_review(**args)
                self.assertFalse(args['output_root'].exists())
                self.assertEqual(calls, [])
                result = review.run_review(**args, execute=True)
                self.assertEqual(result['status'], 'complete')
                self.assertEqual(result['usage']['requests'], 7)
                self.assertEqual(result['decision_counts']['claude'], {'keep': 1, 'reject': 1, 'uncertain': 1})
                self.assertFalse(result['p3_accepted'])
                self.assertFalse(result['draft_exported'])
                self.assertEqual(calls.count(('claude', 0)), 2)
                self.assertTrue(all(calls.count((j, i)) == 1 for j in ('gpt', 'claude') for i in (1, 2)))
                self.assertEqual(review.run_review(**args, execute=True), result)
                self.assertEqual(len(calls), 7)
                workspace = Path(plan['workspace'])
                self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in workspace.iterdir()))
                self.assertFalse((workspace / 'train.draft.jsonl').exists())
                (workspace / 'cases.json').write_text('{}')
                with self.assertRaises(TopicContractError):
                    review.run_review(**args, execute=True)

    def test_identity_drift_stops_and_is_not_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            args, _ = self.prepare(Path(tmp))
            self.helper.fault = lambda name, batch, model, labels: ('unknown-model', labels)
            before = len(self.helper.calls)
            with patch.object(review, 'urlopen', side_effect=self.helper.provider):
                result = review.run_review(**args, execute=True)
            self.assertEqual(result['circuit_reason'], 'model_identity_mismatch')
            self.assertEqual(result['reviewed'], 0)
            self.assertLessEqual(len(self.helper.calls) - before, 2)
            self.assertEqual(result['status'], 'incomplete')

    def test_fresh_authorization_and_calibration_required_before_reading_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            args, _ = self.prepare(Path(tmp))
            with patch.object(review, 'load_packet') as private, patch.object(review, 'urlopen') as network:
                with self.assertRaises(TopicContractError):
                    review.run_review(**{**args, 'authorization_reference': 'different'})
                (args['calibration'] / 'report.json').write_text('{}')
                with self.assertRaises(TopicContractError):
                    review.run_review(**args)
                private.assert_not_called()
                network.assert_not_called()

    def client(self, root):
        identity = {'budget': self.policy['prospective_review'], 'judges': self.helper.policy['judges'],
                    'models': {n: j['model'] for n, j in self.helper.policy['judges'].items()},
                    'transport_circuit_statuses': [429, 502], 'transport_circuit_consecutive_errors': 3,
                    'first_batches': []}
        auth = root / 'auth.json'
        auth.write_text('{"OPENAI_API_KEY":"fixture"}')
        workspace = root / 'workspace'
        workspace.mkdir()
        return review.Requests(workspace, identity, 'https://fixture.invalid', auth), identity

    def test_interrupted_reservation_not_redispatched_and_raw_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, identity = self.client(Path(tmp))
            row = self.candidates[0]
            batch = {'judge': 'gpt', 'ids': [row['sample_id']], 'phase': 'first'}
            payload, sha, estimate = review.payload_record(identity, batch, row)
            client.budget.reserve(sha, estimate)
            with patch.object(review, 'urlopen') as call:
                self.assertEqual(client.call(batch, row), (None, 'interrupted_request_not_redispatched'))
                call.assert_not_called()
            _atomic_json(client.workspace / (sha + '.json'), {'request_digest': sha, 'batch': batch,
                'payload_sha256': 'changed', 'raw_body_base64': None, 'raw_body_sha256': digest(None), 'reservation': 0})
            with self.assertRaises(TopicContractError):
                client.call(batch, row)

    def test_repeated_429_circuit_persists_and_budget_exhaustion_never_dispatches(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, identity = self.client(Path(tmp))
            with patch.object(review, 'urlopen', side_effect=HTTPError('https://fixture.invalid', 429, 'limited', {}, None)) as call:
                for row in self.candidates[:4]:
                    client.call({'judge': 'gpt', 'ids': [row['sample_id']], 'phase': 'first'}, row)
                self.assertEqual(call.call_count, 3)
                self.assertEqual(client.state['reason'], 'repeated_transport_errors')
            replay = review.Requests(client.workspace, identity, 'https://fixture.invalid', Path(tmp) / 'auth.json')
            self.assertTrue(replay.circuit.is_set())
        with tempfile.TemporaryDirectory() as tmp:
            client, identity = self.client(Path(tmp))
            client.budget.policy = {**identity['budget'], 'max_requests': 0}
            with patch.object(review, 'urlopen') as call:
                self.assertEqual(client.call({'judge': 'gpt', 'ids': [self.candidates[0]['sample_id']], 'phase': 'first'},
                                            self.candidates[0]), (None, 'budget_exhausted'))
                call.assert_not_called()

    def test_missing_packet_hash_and_source_tampering_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = Path(tmp)
            (packet / 'manifest.json').write_text('{}')
            config = review.load_config(ROOT / 'configs/topic-validation-review-v1.yaml')
            with self.assertRaisesRegex(TopicContractError, 'frozen population'):
                review.load_packet(packet, config, self.policy, {}, packet / 'consent')

    def test_many_failures_use_exactly_40_repairs_without_recursive_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            args, _ = self.prepare(Path(tmp))
            membership = {r['sample_id']: review.preparation.strata_for(r) for r in self.candidates}
            calls = Counter()
            def provider(request, **kwargs):
                response = self.helper.provider(request, **kwargs)
                key = self.helper.calls[-1][0], self.helper.calls[-1][1][0]
                calls[key] += 1
                value = json.loads(response.getvalue())
                if calls[key] == 1:
                    if 'choices' in value:
                        value['choices'] = [{'finish_reason': 'length', 'message': {'role': 'assistant'}}]
                    else:
                        value['status'] = 'incomplete'
                return io.BytesIO(json.dumps(value).encode())
            with patch.object(review, 'load_packet', return_value=(
                    {'identity': {'source_digests': {}}}, self.candidates, membership)), \
                 patch.object(review, 'urlopen', side_effect=provider):
                result = review.run_review(**args, execute=True)
                self.assertEqual(result['status'], 'incomplete')
                self.assertEqual(result['usage']['requests'], 48 + 40)
                self.assertEqual(result['reviewed'], 40)
                self.assertEqual(result['unresolved_response_failures'], 8)
                self.assertEqual(max(calls.values()), 2)
                self.assertEqual(sorted(k for k, n in calls.items() if n == 2), sorted(calls)[:40])
                self.assertEqual(review.run_review(**args, execute=True), result)
                self.assertEqual(sum(calls.values()), 88)


if __name__ == '__main__':
    unittest.main()
