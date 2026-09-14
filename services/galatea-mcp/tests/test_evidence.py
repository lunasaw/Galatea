import json
import helpers
from helpers import finish, campaign


class EvidenceTests(helpers.ServiceFixture):
    def baseline(self, **kwargs):
        op = self.submit(self.plan())
        rid = finish(self.service, self.ray, self.evidence, 'campaign1', op['operation_id'], **kwargs)
        return op, rid

    def freeze(self, rid):
        compare = self.call('compare_runs', run_ids=[rid])
        return self.call('freeze_candidate', run_id=rid, evidence_digest=compare['ranking'][0]['evidence_digest'])

    def final_stages(self, candidate):
        p = self.call('plan_run', step_id='champion', role='champion', attempt=1, config_id='c1', release_id='r1')
        op = self.submit(p['plan_id'])
        rid = finish(self.service, self.ray, self.evidence, 'campaign1', op['operation_id'])
        self.call('verify_candidate', candidate_id=candidate['candidate_id'])
        ep = self.call('plan_run', step_id='evaluate', role='evaluate', attempt=1, config_id='c1', release_id='r1')
        return self.submit(ep['plan_id'])

    def test_full_delivery_requires_clean_champion_and_final_evaluation(self):
        _, rid = self.baseline()
        candidate = self.freeze(rid)
        early = self.call('verify_candidate', candidate_id=candidate['candidate_id'])
        self.assertEqual(early['outcome'], 'best-effort')
        self.assertEqual(early['final_test_status'], 'not-run')
        evaluation = self.final_stages(candidate)
        finish(self.service, self.ray, self.evidence, 'campaign1', evaluation['operation_id'])
        report = self.call('verify_candidate', candidate_id=candidate['candidate_id'])
        self.assertEqual(report['outcome'], 'accepted')
        self.assertEqual(report['integrity'], 'verified')
        self.assertEqual(self.call('get_campaign')['report']['report_ref'], report['report_ref'])

    def test_corrupt_artifact_is_never_deliverable(self):
        from galatea_mcp.errors import DomainError
        _, rid = self.baseline(intact=False)
        result = self.call('compare_runs', run_ids=[rid])
        self.assertEqual(result['ranking'], [])
        self.assertEqual(result['rejected'][0]['reason'], 'artifact-integrity')
        with self.assertRaises(DomainError):
            self.call('freeze_candidate', run_id=rid, evidence_digest='a' * 64)

    def test_cross_campaign_run_and_test_metric_are_inaccessible(self):
        from galatea_mcp.errors import DomainError
        _, rid = self.baseline()
        self.evidence.runs[rid]['metrics']['test_secret'] = 123
        rows = self.call('query_runs')['items']
        self.assertNotIn('test_secret', rows[0]['metrics'])
        with self.assertRaisesRegex(DomainError, 'forbidden'):
            self.call('get_metric_history', run_id=rid, metric='test_secret')
        self.evidence.runs[rid]['campaign_id'] = 'foreign'
        with self.assertRaisesRegex(DomainError, 'forbidden'):
            self.call('get_artifact', run_id=rid, artifact_ref='reports/evidence.json')

    def test_candidate_replay_and_evidence_digest_tampering(self):
        from galatea_mcp.errors import DomainError
        _, rid = self.baseline()
        with self.assertRaisesRegex(DomainError, 'evidence-digest'):
            self.call('freeze_candidate', run_id=rid, evidence_digest='0' * 64)
        c1 = self.freeze(rid)
        self.assertEqual(self.freeze(rid), c1)

    def test_non_promotable_run_cannot_be_frozen(self):
        from galatea_mcp.errors import DomainError
        from galatea_mcp.projects import Registry

        self.registry_data['projects'][0]['configs']['c1']['promotable'] = False
        self.registry = Registry(self.registry_data, helpers.FakeObjects())
        self.service.registry = self.registry
        _, rid = self.baseline()
        comparison = self.call('compare_runs', run_ids=[rid])
        with self.assertRaisesRegex(DomainError, 'candidate-ineligible'):
            self.call(
                'freeze_candidate',
                run_id=rid,
                evidence_digest=comparison['ranking'][0]['evidence_digest'],
            )

    def test_gate_failure_does_not_unlock_search_or_repeat_evaluation(self):
        from galatea_mcp.errors import DomainError
        _, rid = self.baseline()
        candidate = self.freeze(rid)
        op = self.final_stages(candidate)
        finish(self.service, self.ray, self.evidence, 'campaign1', op['operation_id'], metric=0.8)
        report = self.call('verify_candidate', candidate_id=candidate['candidate_id'])
        self.assertEqual(report['outcome'], 'best-effort')
        self.assertEqual(report['final_test_status'], 'failed')
        with self.assertRaises(DomainError):
            self.call('plan_run', step_id='trial1', role='trial', attempt=1, config_id='c1', release_id='r1')
        self.assertEqual(len(self.ray.jobs), 3)

    def test_marker_cross_campaign_conflict(self):
        from galatea_mcp.errors import DomainError
        from galatea_mcp.auth import Principal
        _, rid = self.baseline()
        candidate = self.freeze(rid)
        op = self.final_stages(candidate)
        finish(self.service, self.ray, self.evidence, 'campaign1', op['operation_id'])
        c = self.store.read('campaigns', 'campaign1')
        c['spec']['campaign_id'] = 'campaign2'
        c['operations'] = {}
        c['plans'] = {}
        c['stage'] = 'champion_ready'
        self.store.save('campaigns', 'campaign2', c)
        self.principal = Principal('user', frozenset({'p1'}), frozenset({'campaign1','campaign2'}), frozenset({'*'}))
        args = {'project_id':'p1', 'campaign_id':'campaign2', 'step_id':'evaluate', 'role':'evaluate',
                'attempt':1, 'config_id':'c1', 'release_id':'r1'}
        plan = self.service.call(self.principal, 'galatea_plan_run', args)
        with self.assertRaisesRegex(DomainError, 'holdout-used'):
            self.service.call(self.principal, 'galatea_submit_job', {k:args[k] for k in ['project_id','campaign_id']} |
                              {'plan_id':plan['plan_id'], 'idempotency_key':'another'})
        self.assertEqual(len(self.ray.jobs), 3)

    def test_artifact_paths_and_incompatible_evidence_fail_closed(self):
        from galatea_mcp.errors import DomainError
        _, rid = self.baseline()
        for path in ['../secret', 'file:///etc/passwd', '%2e%2e/secret', 'reports/../../test.json']:
            with self.assertRaises(DomainError):
                self.call('get_artifact', run_id=rid, artifact_ref=path)
        raw = json.loads(self.evidence.artifacts[(rid, 'reports/evidence.json')])
        raw['lineage']['split_digest'] = 'e' * 64
        self.evidence.artifacts[(rid, 'reports/evidence.json')] = json.dumps(raw).encode()
        self.assertEqual(self.call('compare_runs', run_ids=[rid])['ranking'], [])
