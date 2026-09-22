from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

import test_topic_repair_review as fixtures
from wechat_persona import topic_repair_recovery as recovery
from wechat_persona import topic_repair_export as export
from wechat_persona import topic_repair_review as review
from wechat_persona._common import digest, file_digest
from wechat_persona.fact_review_server import _atomic_json
from wechat_persona.topic_candidates import read_json, rows
from wechat_persona.topic_context import TopicContractError
from wechat_persona.topic_repair_protocol import validate_axes


class RepairRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.RepairReviewRunnerTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f = self.f
        for index in (1, 2):
            row = deepcopy(f.candidates[0])
            row['sample_id'] += str(index)
            row['parent_sample_id'] += str(index)
            row['candidate_sha256'] = digest({k: v for k, v in row.items() if k != 'candidate_sha256'})
            f.candidates.append(row)
            case = deepcopy(f.dispositions[0])
            case.update(parent_sample_id=row['parent_sample_id'], candidate_sha256=row['candidate_sha256'])
            f.dispositions.append(case)
        inputs = {}
        for path in (f.args['queue'] / 'manifest.json', f.args['repair'] / 'manifest.json', f.args['consent']):
            path.parent.mkdir(exist_ok=True)
            _atomic_json(path, {'fixture': path.name})
            inputs[str(path)] = file_digest(path)
        _atomic_json(f.args['repair'] / 'context-comparison.json', {'cases': [
            {'parent_sample_id': r['parent_sample_id'], 'input_changed': True} for r in f.candidates]})
        p = patch.object(review, 'load_sources', return_value=(f.candidates, f.dispositions, inputs))
        p.start()
        self.addCleanup(p.stop)

        def outage(model, axes):
            if len(f.calls) > 2:
                return {'http_status': 502, 'error_type': 'HTTPError',
                        'raw_body_base64': '', 'raw_body_sha256': digest('')}

        f.fault = outage
        self.parent_report = review.run_review(**f.args, execute=True)
        self.parent = Path(self.parent_report['workspace'])
        self.assertEqual(self.parent_report['reviewed'], 2)
        self.assertEqual(self.parent_report['circuit_reason'], 'repeated_transport_errors')
        f.fault = None
        self.config = recovery.load_config(fixtures.ROOT / 'configs/topic-repair-recovery-v1.yaml')
        self.config.update(parent_manifest_sha256=file_digest(self.parent / 'manifest.json'),
                           inherited_judgments=2, missing_judgments=4, previous_attempted_failures=3, workers=1)
        p = patch.object(recovery, 'load_config', return_value=self.config)
        p.start()
        self.addCleanup(p.stop)
        probe = f.root / 'new-preflight'
        probe.mkdir()
        _atomic_json(probe / 'identity.json', {'authorization_reference': 'test-recovery'})
        _atomic_json(probe / 'manifest.json', {'synthetic_probe': True})
        self.args = {k: v for k, v in f.args.items() if k not in ('original_preflight', 'calibration')}
        self.args.update(parent=self.parent, preflight=probe, authorization_reference='test-recovery',
                         config_path=fixtures.ROOT / 'configs/topic-repair-recovery-v1.yaml',
                         output_root=f.root / 'recovery')
        self.export_args = {k: f.args[k] for k in ('queue', 'repair', 'consent', 'controlled_root')}
        self.export_args['output_root'] = f.root / 'draft'

    def test_missing_only_cumulative_budget_offline_replay_and_export(self):
        plan = recovery.run_recovery(**self.args)
        self.assertFalse(Path(plan['workspace']).exists())
        self.assertEqual(plan['planned_first_requests'], 4)
        self.assertEqual(plan['budget']['max_requests'], self.f.config['budget']['max_requests'] - 5)
        self.assertEqual(plan['budget']['max_repair_requests'], 1)
        self.assertEqual(plan['budget']['max_input_tokens'], self.f.config['budget']['max_input_tokens']
                         - self.parent_report['usage']['charged_input_tokens'])
        report = recovery.run_recovery(**self.args, execute=True)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['reviewed'], 6)
        self.assertEqual(len(self.f.calls), 9)
        self.assertEqual(report['cumulative_usage']['requests'], 9)
        machine = Path(report['workspace'])
        sha = file_digest(machine / 'manifest.json')
        with patch.object(review, 'capture_http', side_effect=AssertionError('network')):
            self.assertEqual(recovery.run_recovery(**self.args, execute=True), report)
            combined, missing, audit = recovery.replay_complete(machine, self.f.candidates, self.f.dispositions)
            self.assertFalse(missing)
            inherited = read_json(self.parent / 'decisions.json')['decisions']
            self.assertTrue(all(row in combined for row in inherited))
            self.assertEqual(audit['cumulative_usage'], report['cumulative_usage'])
            compiled = export.compile_repaired_draft(machine=machine, **self.export_args, execute=True)
            self.assertEqual(compiled['selected_count'], 3)
            self.assertFalse(compiled['formal_training_eligible'])
            self.assertEqual(export.compile_repaired_draft(machine=machine, **self.export_args, execute=True), compiled)
        self.assertEqual(file_digest(machine / 'manifest.json'), sha)
        self.assertEqual(file_digest(self.parent / 'manifest.json'), self.config['parent_manifest_sha256'])
        source = {c['sample_id']: c for c in self.f.candidates}
        for row in rows(Path(compiled['output_dir']) / 'train.draft.jsonl'):
            candidate = source[row['parent_sample_id']]
            for key in ('messages', 'context_messages', 'target_messages', 'tokens'):
                self.assertEqual(row[key], candidate[key])
        identity = read_json(machine / 'identity.json')
        identity['budget']['max_requests'] += 1
        _atomic_json(machine / 'identity.json', identity)
        manifest = read_json(machine / 'manifest.json')
        manifest['identity'] = identity
        manifest['output_digests']['identity.json'] = file_digest(machine / 'identity.json')
        manifest.pop('manifest_sha256')
        manifest['manifest_sha256'] = digest(manifest)
        _atomic_json(machine / 'manifest.json', manifest)
        with self.assertRaisesRegex(TopicContractError, 'lineage'):
            recovery.replay_complete(machine, self.f.candidates, self.f.dispositions)

    def test_previous_failures_cannot_get_a_third_attempt(self):
        def invalid(model, axes):
            axes['responds_to_indices'] = [99]

        self.f.fault = invalid
        report = recovery.run_recovery(**self.args, execute=True)
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(report['usage']['requests'], 5)
        self.assertEqual(report['repair_requests'], 1)
        self.assertEqual(report['reviewed'], 2)
        with self.assertRaisesRegex(TopicContractError, 'incomplete'):
            export.compile_repaired_draft(machine=Path(report['workspace']), **self.export_args, execute=True)
        self.assertFalse(self.export_args['output_root'].exists())

    def test_recovery_requires_new_probe_and_matching_authority(self):
        with self.assertRaises(TopicContractError):
            recovery.run_recovery(**{**self.args, 'preflight': self.f.args['preflight'],
                                     'authorization_reference': 'test-authority'})
        with self.assertRaises(TopicContractError):
            recovery.run_recovery(**{**self.args, 'authorization_reference': 'wrong'})
        self.assertEqual(len(self.f.calls), 5)

    def test_model_drift_seals_recovery_and_blocks_export(self):
        import base64
        import json

        def drift(model, axes):
            encoded = base64.b64encode(json.dumps({'model': 'different-model',
                'usage': {'input_tokens': 100, 'output_tokens': 20}}).encode()).decode()
            return {'http_status': 200, 'error_type': None, 'raw_body_base64': encoded,
                    'raw_body_sha256': digest(encoded)}

        self.f.fault = drift
        report = recovery.run_recovery(**self.args, execute=True)
        self.assertEqual(report['circuit_reason'], 'model_identity_mismatch')
        self.assertEqual(report['reviewed'], 2)
        self.assertEqual(len(self.f.calls), 6)
        with patch.object(review, 'capture_http', side_effect=AssertionError('network')):
            self.assertEqual(recovery.run_recovery(**self.args, execute=True), report)
        with self.assertRaises(TopicContractError):
            export.compile_repaired_draft(machine=Path(report['workspace']), **self.export_args, execute=True)
        self.assertFalse(self.export_args['output_root'].exists())

    def test_reserved_but_missing_raw_is_not_sent_again(self):
        with patch.object(review, 'capture_http', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):
                recovery.run_recovery(**self.args, execute=True)
        report = recovery.run_recovery(**self.args, execute=True)
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(report['response_failure_counts'], {'interrupted_request_not_redispatched': 1})
        self.assertEqual(len(self.f.calls), 8)
        with patch.object(review, 'capture_http', side_effect=AssertionError('network')):
            self.assertEqual(recovery.run_recovery(**self.args, execute=True), report)


class RepairExportTests(unittest.TestCase):
    def test_draft_requires_exact_pair_anchor_and_candidate_digest(self):
        f = fixtures.RepairProtocolTests()
        f.setUp()
        pair = [{**validate_axes(f.row, f.axes), 'judge': j} for j in ('gpt', 'claude')]
        disposition = {'parent_sample_id': f.row['parent_sample_id'], 'candidate_sha256': f.row['candidate_sha256'],
                       'prior_hard_risks': [], 'previous_disposition': 'selected'}
        case = review.summarize([f.row], pair, [disposition])['cases'][0]
        bound = export.bind_reviewed_candidate(f.row, case, pair, 'a' * 64)
        self.assertEqual(bound['messages'], f.row['messages'])
        self.assertFalse(bound['reply_link']['independent_quality_validation_completed'])
        for change in ({'prior_hard_risks': ['privacy']}, {'candidate_sha256': 'b' * 64},
                       {'shared_responds_to_ids': []}, {'decision_sha256': {'gpt': 'x', 'claude': 'y'}}):
            with self.subTest(change=change), self.assertRaises(TopicContractError):
                export.bind_reviewed_candidate(f.row, {**case, **change}, pair, 'a' * 64)


if __name__ == '__main__':
    unittest.main()
