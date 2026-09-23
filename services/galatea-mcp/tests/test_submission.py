import copy
import tempfile
import unittest
from pathlib import Path
import helpers


class SubmissionTests(helpers.ServiceFixture):
    def test_duplicate_key_and_replan_do_not_duplicate_compute_or_budget(self):
        p1 = self.plan()
        op1 = self.submit(p1)
        op2 = self.submit(self.plan(), 'different-key')
        self.assertEqual(op1['operation_id'], op2['operation_id'])
        self.assertEqual(len(self.ray.jobs), 1)
        self.assertEqual(self.call('get_campaign')['budget']['reserved_cpu_seconds'], 12)

    def test_lost_response_recovers_same_submission_after_restart(self):
        from galatea_mcp.service import Service
        self.ray.lose_response = True
        op = self.submit(self.plan())
        self.assertEqual(op['execution'], 'unknown')
        restart = Service(self.store, self.registry, self.ray, self.evidence, clock=lambda: 101)
        restart.reconcile_all()
        recovered = self.call('get_operation', operation_id=op['operation_id'])
        self.assertEqual(recovered['execution'], 'queued')
        self.assertEqual(len(self.ray.jobs), 1)

    def test_missing_history_never_resubmits_ambiguous_compute(self):
        op = self.submit(self.plan())
        self.ray.jobs.clear()
        self.service.reconcile_all()
        self.assertEqual(self.call('get_operation', operation_id=op['operation_id'])['execution'], 'unknown')
        self.submit(self.plan(), 'another')
        self.assertEqual(self.ray.jobs, {})

    def test_cancellation_is_durable_and_precedes_stop(self):
        from galatea_mcp.errors import DomainError
        self.submit(self.plan())
        self.call('cancel_campaign', reason='user cancel')
        self.assertTrue(self.call('get_campaign')['cancelled'])
        with self.assertRaisesRegex(DomainError, 'cancelled'):
            self.plan()

    def test_input_drift_invalidates_existing_plan(self):
        from galatea_mcp.errors import DomainError
        p = self.plan()
        Path(self.registry_data['projects'][0]['root'], 'config.json').write_text('{}')
        with self.assertRaisesRegex(DomainError, 'digest-mismatch'):
            self.submit(p)
        self.assertFalse(self.ray.jobs)

    def test_scope_and_unknown_fields_cannot_grant_authorization(self):
        from galatea_mcp.auth import Principal
        from galatea_mcp.errors import DomainError
        outsider = Principal('outsider', frozenset({'p2'}), frozenset({'campaign1'}), frozenset({'*'}))
        with self.assertRaisesRegex(DomainError, 'forbidden'):
            self.service.call(outsider, 'galatea_get_campaign', {'project_id': 'p1', 'campaign_id': 'campaign1'})
        with self.assertRaisesRegex(DomainError, 'invalid-input'):
            self.plan(approved=True)

    def test_preserves_final_stage_budget_and_single_active_job(self):
        from galatea_mcp.errors import DomainError
        self.submit(self.plan())
        p = self.store.read('campaigns', 'campaign1')
        # Even another admitted slot cannot overlap the first job.
        with self.assertRaisesRegex(DomainError, 'stage|active'):
            self.call('plan_run', step_id='trial1', attempt=1, release_id='r1', config_id='c1', role='trial')
        self.assertEqual(len(self.ray.jobs), 1)

    def test_attempt_requires_confirmed_failure(self):
        from galatea_mcp.errors import DomainError
        with self.assertRaisesRegex(DomainError, 'retry'):
            self.call('plan_run', step_id='base', attempt=2, release_id='r1', config_id='c1', role='baseline')

    def test_cluster_change_cannot_attach_same_id(self):
        op = self.submit(self.plan())
        self.ray.jobs[op['submission_id']]['cluster_id'] = 'other'
        self.service.reconcile_all()
        self.assertEqual(self.call('get_operation', operation_id=op['operation_id'])['execution'], 'unknown')

    def test_observe_receipt_without_committed_operation_never_dispatches(self):
        plan = self.plan()
        args = {'project_id': 'p1', 'campaign_id': 'campaign1', 'plan_id': plan, 'idempotency_key': 'observe'}
        self.assertIsNone(self.service.observe_receipt(self.principal, 'galatea_submit_job', args))
        self.assertFalse(self.ray.jobs)
        self.assertEqual(self.call('get_campaign')['budget']['reserved_cpu_seconds'], 0)

    def test_observe_receipt_keeps_ambiguous_operation_unknown(self):
        plan = self.plan()
        self.ray.lose_response = True
        operation = self.submit(plan)
        args = {'project_id': 'p1', 'campaign_id': 'campaign1', 'plan_id': plan, 'idempotency_key': 'observe'}
        self.assertEqual(operation['execution'], 'unknown')
        self.assertIsNone(self.service.observe_receipt(self.principal, 'galatea_submit_job', args))
        self.assertEqual(len(self.ray.jobs), 1)

    def test_observe_receipt_rechecks_authorization(self):
        from galatea_mcp.auth import Principal
        from galatea_mcp.errors import DomainError
        plan = self.plan()
        self.submit(plan)
        args = {'project_id': 'p1', 'campaign_id': 'campaign1', 'plan_id': plan, 'idempotency_key': 'observe'}
        outsider = Principal('outsider', frozenset({'p1'}), frozenset(), frozenset({'*'}))
        with self.assertRaisesRegex(DomainError, 'forbidden'):
            self.service.observe_receipt(outsider, 'galatea_submit_job', args)
        self.assertEqual(len(self.ray.jobs), 1)
