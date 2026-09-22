from __future__ import annotations

import base64
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_topic_repair_recovery as fixtures
from wechat_persona import topic_repair_export as export
from wechat_persona import topic_repair_recovery_v2 as recovery
from wechat_persona import topic_repair_review as review
from wechat_persona._common import digest, file_digest
from wechat_persona.fact_review_server import _atomic_json
from wechat_persona.topic_context import TopicContractError


class RepairRecoveryV2Tests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.RepairRecoveryTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

        def drift_before_private_recovery(model, axes):
            if len(self.f.f.calls) == 6:
                encoded = base64.b64encode(json.dumps({
                    'model': 'gpt-6-sol',
                    'usage': {'input_tokens': 100, 'output_tokens': 20},
                }).encode()).decode()
                return {'http_status': 200, 'error_type': None, 'raw_body_base64': encoded,
                        'raw_body_sha256': digest(encoded)}

        self.f.f.fault = drift_before_private_recovery
        current_report = fixtures.recovery.run_recovery(**self.f.args, execute=True)
        self.assertEqual(current_report['circuit_reason'], 'model_identity_mismatch')
        self.assertEqual(current_report['reviewed'], 2)
        self.parent = Path(current_report['workspace'])
        self.f.f.fault = None

        self.preflights = []
        for index in range(3):
            path = self.f.f.root / f'stable-preflight-{index}'
            path.mkdir()
            _atomic_json(path / 'identity.json', {'authorization_reference': 'test-recovery-v2'})
            _atomic_json(path / 'manifest.json', {'probe': index})
            self.preflights.append(path)
        self.config = {
            'schema_version': 'topic-repair-recovery-config-v2',
            'parent_manifest_sha256': file_digest(self.parent / 'manifest.json'),
            'inherited_judgments': 2,
            'missing_judgments': 4,
            'ancestor_attempt_counts': {'zero': 0, 'one': 4, 'two': 0},
            'workers': 1,
            'max_total_attempts_per_judgment': 2,
            'required_consecutive_route_preflights': 3,
            'require_same_exact_models': True,
            'reuse_original_total_budget': True,
            'governance': review.GOVERNANCE,
        }
        config_patch = patch.object(recovery, 'load_config', return_value=self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.args = {key: value for key, value in self.f.args.items()
                     if key not in ('parent', 'preflight')}
        self.args.update(parent=self.parent, preflights=self.preflights,
                         config_path=fixtures.fixtures.ROOT / 'configs/topic-repair-recovery-v2.yaml',
                         output_root=self.f.f.root / 'recovery-v2',
                         authorization_reference='test-recovery-v2')

    def test_stable_route_recovers_only_missing_and_exports(self):
        plan = recovery.run_recovery(**self.args)
        self.assertEqual(plan['planned_first_requests'], 4)
        self.assertEqual(plan['planned_repair_capacity'], 0)
        report = recovery.run_recovery(**self.args, execute=True)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['reviewed'], 6)
        self.assertFalse(report['valid_judgments_reasked'])
        machine = Path(report['workspace'])
        with patch.object(review, 'capture_http', side_effect=AssertionError('network')):
            self.assertEqual(recovery.run_recovery(**self.args, execute=True), report)
            decisions, errors, audit = recovery.replay_complete(
                machine, self.f.f.candidates, self.f.f.dispositions)
            self.assertEqual(len(decisions), 6)
            self.assertFalse(errors)
            self.assertEqual(audit['cumulative_usage'], report['cumulative_usage'])
            compiled = export.compile_repaired_draft(
                machine=machine, **self.f.export_args, execute=True)
        self.assertEqual(compiled['selected_count'], 3)
        self.assertFalse(compiled['formal_training_eligible'])

    def test_requires_three_distinct_matching_authority_preflights(self):
        with self.assertRaisesRegex(TopicContractError, 'distinct'):
            recovery.run_recovery(**{**self.args, 'preflights': self.preflights[:2]})
        _atomic_json(self.preflights[2] / 'identity.json', {'authorization_reference': 'wrong'})
        with self.assertRaisesRegex(TopicContractError, 'authority'):
            recovery.run_recovery(**self.args)

    def test_second_failure_is_not_given_a_third_attempt(self):
        def invalid(model, axes):
            axes['responds_to_indices'] = [99]

        self.f.f.fault = invalid
        report = recovery.run_recovery(**self.args, execute=True)
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(report['reviewed'], 2)
        self.assertEqual(report['usage']['requests'], 4)
        with self.assertRaisesRegex(TopicContractError, 'incomplete'):
            export.compile_repaired_draft(
                machine=Path(report['workspace']), **self.f.export_args, execute=True)


if __name__ == '__main__':
    unittest.main()
