from __future__ import annotations

from collections import Counter
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import test_topic_validation_review as fixtures
from wechat_persona import topic_validation_recovery as recovery
from wechat_persona import topic_validation_review as previous
from wechat_persona._common import digest, file_digest
from wechat_persona.fact_review_server import _atomic_json
from wechat_persona.topic_candidates import read_json
from wechat_persona.topic_context import TopicContractError
from wechat_persona.topic_validation_budget import GuardedBudget, GuardedRequests, input_reservation, request_table


class ValidationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ValidationReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.config = recovery.load_config(ROOT / 'configs/topic-validation-recovery-v1.yaml')

    def prepare(self, root):
        args, population = self.fixture.prepare(root)
        missing = {('gpt', population[0]['sample_id']), ('claude', population[1]['sample_id'])}
        def parent_provider(request, **kwargs):
            response = self.fixture.helper.provider(request, **kwargs)
            judge, ids = self.fixture.helper.calls[-1]
            if (judge, ids[0]) in missing:
                raise URLError('synthetic transport failure')
            return response
        def fault(name, batch, model, labels):
            for case, label in zip(batch, labels):
                if case['id'] == population[1]['sample_id']:
                    label['communicative_value'] = 'low_signal'
                if case['id'] == population[2]['sample_id']:
                    label.update(context_status='missing_referent', uncertainties=[{
                        'axis': 'context', 'issue': 'missing_referent', 'source_indices': [0],
                        'reply_quote': case['reply']}])
            return model, labels
        self.fixture.helper.fault = fault
        with patch.object(previous, 'urlopen', side_effect=parent_provider):
            parent_result = previous.run_review(**args, execute=True)
        self.assertEqual(parent_result['reviewed'], 4)
        parent = Path(parent_result['workspace'])
        with patch.object(previous.base, 'urlopen', side_effect=self.fixture.helper.provider):
            fresh = previous.base.run_review(scope='preflight', config_path=self.fixture.helper.config,
                output_root=root / 'out', controlled_root=root, confidence_audit=root / 'audit-basis',
                base_url=args['base_url'], auth_file=args['auth_file'],
                authorization_reference='new-recovery-run', execute=True)
        config = deepcopy(self.config)
        config.update(parent_manifest_sha256=file_digest(parent / 'manifest.json'), inherited_judgments=4,
                      missing_by_judge={'gpt': 1, 'claude': 1})
        patcher = patch.object(recovery, 'load_config', return_value=config)
        patcher.start()
        self.addCleanup(patcher.stop)
        return {**args, 'parent': parent, 'preflight': Path(fresh['workspace']),
                'config_path': ROOT / 'configs/topic-validation-recovery-v1.yaml',
                'authorization_reference': 'new-recovery-run', 'output_root': root / 'recovery'}, population, missing

    def client(self, root, count=4):
        _, identity = self.fixture.client(root)
        candidates = self.fixture.candidates[:count]
        identity.update(budget=deepcopy(self.config['budget']), input_guard=deepcopy(self.config['input_guard']),
                        first_batches=[{'judge': 'gpt', 'ids': [r['sample_id']], 'phase': 'first'} for r in candidates])
        table = request_table(identity, candidates)
        workspace = root / 'workspace'
        _atomic_json(workspace / 'budget.json', {'identity_sha256': digest(identity), 'attempts': []})
        return GuardedRequests(workspace, identity, 'https://fixture.invalid', root / 'auth.json', table), identity, candidates

    def test_plan_is_read_only_then_only_missing_judgments_are_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            args, population, missing = self.prepare(Path(tmp))
            snapshots = {p: file_digest(p) for p in args['parent'].iterdir() if p.is_file()}
            calls = Counter()
            def provider(request, **kwargs):
                response = self.fixture.helper.provider(request, **kwargs)
                judge, ids = self.fixture.helper.calls[-1]
                key = judge, ids[0]
                self.assertIn(key, missing)
                calls[key] += 1
                if judge == 'gpt' and calls[key] == 1:
                    raise URLError('synthetic first failure')
                return response
            with patch.object(previous, 'urlopen', side_effect=provider):
                plan = recovery.run_recovery(**args)
                self.assertFalse(args['output_root'].exists())
                self.assertEqual(calls, {})
                result = recovery.run_recovery(**args, execute=True)
                self.assertEqual(result['status'], 'complete')
                self.assertEqual(result['reviewed'], 6)
                self.assertEqual(result['new_valid_judgments'], 2)
                self.assertEqual(result['usage']['requests'], 3)
                self.assertEqual(result['repair_requests'], 1)
                self.assertTrue(result['inherited_judgments_unchanged'])
                self.assertEqual(result['decision_counts']['gpt'], {'keep': 1, 'reject': 1, 'uncertain': 1})
                self.assertFalse(result['draft_exported'])
                self.assertFalse(result['p3_accepted'])
                self.assertEqual(recovery.run_recovery(**args, execute=True), result)
                self.assertEqual(sum(calls.values()), 3)
            self.assertTrue(all(file_digest(p) == sha for p, sha in snapshots.items()))
            workspace = Path(plan['workspace'])
            self.assertEqual(workspace.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in workspace.iterdir()))
            (workspace / 'cases.json').write_text('{}')
            with self.assertRaises(TopicContractError):
                recovery.run_recovery(**args, execute=True)

    def test_no_recursive_repair_and_incomplete_replay_is_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            args, _, _ = self.prepare(Path(tmp))
            with patch.object(previous, 'urlopen', side_effect=URLError('synthetic')) as network:
                result = recovery.run_recovery(**args, execute=True)
                self.assertEqual(result['status'], 'incomplete')
                self.assertEqual(result['usage']['requests'], 4)
                self.assertEqual(result['repair_requests'], 2)
                self.assertEqual(result['unresolved_response_failures'], 2)
                self.assertEqual(recovery.run_recovery(**args, execute=True), result)
                self.assertEqual(network.call_count, 4)

    def test_new_authorization_and_unmodified_parent_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            args, _, _ = self.prepare(Path(tmp))
            with patch.object(previous, 'urlopen') as network:
                with self.assertRaisesRegex(TopicContractError, 'new scoped authorization'):
                    recovery.run_recovery(**{**args, 'authorization_reference': 'new-validation-run'})
                with self.assertRaises(TopicContractError):
                    recovery.run_recovery(**{**args, 'authorization_reference': 'unbound-reference'})
                (args['parent'] / 'report.json').write_text('{}')
                with self.assertRaises(TopicContractError):
                    recovery.run_recovery(**args)
                network.assert_not_called()

    def test_model_reservations_cover_provider_overhead_and_hold_inflight_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, identity, candidates = self.client(Path(tmp))
            table = request_table(identity, candidates)
            entries = [(sha, row) for sha, row in table.items() if row['batch']['phase'] == 'first']
            capacity = sum(row['reserve'] for _, row in entries[:2])
            client.budget.policy = {**identity['budget'], 'max_input_tokens': capacity + identity['input_guard']['headroom_tokens']}
            for sha, row in entries[:2]:
                client.budget.reserve(sha, row['estimate'])
            for index, (_, row) in enumerate(entries[:2]):
                client.budget.settle(index, {'input_tokens': row['estimate'] * 22 // 10, 'output_tokens': 50})
            self.assertEqual(client.budget.usage()['charged_input_tokens'], capacity)
            self.assertIsNone(client.budget.halt_reason())
            with self.assertRaisesRegex(TopicContractError, 'review_budget_or_circuit_breaker'):
                client.budget.reserve(entries[2][0], entries[2][1]['estimate'])
            self.assertGreater(input_reservation(1000, 'gpt', identity['input_guard']),
                               input_reservation(1000, 'claude', identity['input_guard']))

    def test_settlement_overrun_persists_and_stops_new_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client, identity, candidates = self.client(root)
            table = request_table(identity, candidates)
            first = identity['first_batches'][0]
            _, sha, estimate = previous.payload_record(identity, first, candidates[0])
            slot = client.budget.reserve(sha, estimate)
            client.budget.settle(slot, {'input_tokens': table[sha]['reserve'] + 1, 'output_tokens': 50})
            restored = GuardedRequests(client.workspace, identity, client.base_url, root / 'auth.json', table)
            self.assertEqual(restored.state['reason'], 'provider_usage_exceeded_reservation')
            with patch.object(previous, 'urlopen') as network:
                restored.call(identity['first_batches'][1], candidates[1])
                network.assert_not_called()
            with self.assertRaises(TopicContractError):
                restored.budget.settle(slot, {'input_tokens': 1})

    def test_crash_after_raw_before_settlement_reconstructs_usage_circuit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client, identity, candidates = self.client(root)
            first = identity['first_batches'][0]
            _, sha, _ = previous.payload_record(identity, first, candidates[0])
            bound = request_table(identity, candidates)[sha]['reserve']
            def provider(request, **kwargs):
                response = json.loads(self.fixture.helper.provider(request, **kwargs).getvalue())
                response['usage']['input_tokens'] = bound + 100
                return io.BytesIO(json.dumps(response).encode())
            with patch.object(previous, 'urlopen', side_effect=provider), \
                 patch.object(client.budget, 'settle', side_effect=RuntimeError('synthetic interruption')):
                with self.assertRaises(RuntimeError):
                    client.call(first, candidates[0])
            with self.assertRaisesRegex(TopicContractError, 'not settled'):
                recovery.reconstruct(client.workspace, identity, candidates, [])
            decisions, _, audit = recovery.reconstruct(client.workspace, identity, candidates, [], reconcile=True)
            self.assertEqual(len(decisions), 1)
            self.assertTrue(audit['usage_envelope_exceeded'])
            restored = GuardedRequests(client.workspace, identity, client.base_url, root / 'auth.json', request_table(identity, candidates))
            with patch.object(previous, 'urlopen') as network:
                restored.call(identity['first_batches'][1], candidates[1])
                network.assert_not_called()

    def test_reserved_without_response_is_not_redispatched_or_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, identity, candidates = self.client(Path(tmp))
            first = identity['first_batches'][0]
            _, sha, estimate = previous.payload_record(identity, first, candidates[0])
            client.budget.reserve(sha, estimate)
            with patch.object(previous, 'urlopen') as network:
                self.assertEqual(client.call(first, candidates[0])[1], 'interrupted_request_not_redispatched')
                network.assert_not_called()
            _, errors, _ = recovery.reconstruct(client.workspace, identity, candidates, [])
            self.assertNotIn(errors[('gpt', candidates[0]['sample_id'])], previous.REPAIRABLE)

    def test_identity_drift_and_transport_circuit_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, identity, candidates = self.client(Path(tmp))
            with patch.object(previous, 'urlopen', side_effect=HTTPError('https://fixture.invalid', 429, 'limited', {}, None)) as network:
                for batch, candidate in zip(identity['first_batches'], candidates):
                    client.call(batch, candidate)
                self.assertEqual(network.call_count, 3)
                self.assertEqual(client.state['reason'], 'repeated_transport_errors')
        with tempfile.TemporaryDirectory() as tmp:
            client, identity, candidates = self.client(Path(tmp))
            self.fixture.helper.fault = lambda name, batch, model, labels: ('unknown-model', labels)
            with patch.object(previous, 'urlopen', side_effect=self.fixture.helper.provider) as network:
                for batch, candidate in zip(identity['first_batches'], candidates):
                    client.call(batch, candidate)
                self.assertEqual(network.call_count, 1)
                self.assertEqual(client.state['reason'], 'model_identity_mismatch')


if __name__ == '__main__':
    unittest.main()
