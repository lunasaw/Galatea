import copy
import json
import unittest
import helpers


class PolicyTests(helpers.ServiceFixture):
    def test_champion_role_config_may_differ_only_by_role_metadata(self):
        from galatea_mcp.contracts import Configuration
        from galatea_mcp.errors import DomainError
        from pathlib import Path
        p = self.registry.get('p1')
        root = Path(p.root)
        selected = json.loads((root / p.configs['c1'].path).read_text())
        champion = selected | {'run': {'role': 'champion', 'promotable': True},
                               'evaluation': {'test_access': 'untouched'}}
        path = root / 'champion.json'
        path.write_text(json.dumps(champion))
        p.configs['c2'] = Configuration.model_validate(
            p.configs['c1'].model_dump() | {'path': 'champion.json', 'sha256': helpers.sha(path.read_bytes())}
        )
        campaign = self.store.read('campaigns', 'campaign1')
        campaign['stage'] = 'candidate_frozen'
        campaign['candidate'] = {'config_id': 'c1', 'release_id': 'r1'}
        self.service.stage_allowed(campaign, {'role': 'champion', 'config_id': 'c2', 'release_id': 'r1'})
        champion['hyperparameters']['alpha'] = 2.0
        path.write_text(json.dumps(champion))
        p.configs['c2'] = Configuration.model_validate(
            p.configs['c2'].model_dump() | {'sha256': helpers.sha(path.read_bytes())}
        )
        with self.assertRaisesRegex(DomainError, 'candidate-mismatch'):
            self.service.stage_allowed(campaign, {'role': 'champion', 'config_id': 'c2', 'release_id': 'r1'})

    def test_budget_preserves_final_reserve_without_admitting_operation(self):
        from galatea_mcp.errors import DomainError
        c=self.store.read('campaigns','campaign1')
        c['spec']['budget']['cpu_seconds']=35
        self.store.save('campaigns','campaign1',c)
        with self.assertRaisesRegex(DomainError,'budget-exhausted'):
            self.submit(self.plan())
        c=self.store.read('campaigns','campaign1')
        self.assertEqual(c['reserved']['cpu_seconds'],0)
        self.assertEqual(c['operations'],{})

    def test_ray_success_without_evidence_does_not_unlock_search(self):
        from galatea_mcp.errors import DomainError
        op=self.submit(self.plan())
        self.ray.jobs[op['submission_id']]['execution']='succeeded'
        self.service.reconcile_all()
        self.assertEqual(self.call('get_campaign')['stage'],'baseline')
        with self.assertRaisesRegex(DomainError,'stage'):
            self.call('plan_run', step_id='trial1',role='trial',attempt=1,config_id='c1',release_id='r1')

    def test_evaluation_plan_checks_metadata_without_reading_holdout(self):
        from galatea_mcp.projects import Registry
        class Objects:
            def verify(self, ref):
                if ref['key']=='test.json':
                    raise AssertionError('holdout disclosed by plan')
                return True
            def verify_metadata(self, ref):
                return ref['key']=='test.json'
        reg=Registry(self.registry_data,Objects())
        reg.verify('p1','c1','r1','evaluate')

    def test_admin_amend_only_increases_authorized_revision_without_reset(self):
        from galatea_mcp.errors import DomainError
        p=self.plan()
        amendment={'request_revision':2,'expires_at':20000,'approved_by':'operator2',
                   'budget':{'cpu_seconds':72,'gpu_seconds':0,'max_trials':1}}
        self.service.amend('campaign1',amendment)
        self.assertEqual(self.call('get_campaign')['request_revision'],2)
        with self.assertRaisesRegex(DomainError,'stale-plan'):
            self.submit(p)
        self.assertEqual(len(self.store.read('campaigns','campaign1')['plans']),1)
        with self.assertRaisesRegex(DomainError,'revision'):
            self.service.amend('campaign1',amendment)

    def test_evaluator_binding_carries_verified_champion_digest(self):
        from galatea_mcp.backends.binding import BindingSigner
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        op=self.submit(self.plan())
        rid=helpers.finish(self.service,self.ray,self.evidence,'campaign1',op['operation_id'])
        ranking=self.call('compare_runs',run_ids=[rid])['ranking'][0]
        candidate=self.call('freeze_candidate',run_id=rid,evidence_digest=ranking['evidence_digest'])
        plan=self.call('plan_run',step_id='champion',role='champion',attempt=1,config_id='c1',release_id='r1')
        op=self.submit(plan['plan_id'])
        helpers.finish(self.service,self.ray,self.evidence,'campaign1',op['operation_id'])
        self.call('verify_candidate',candidate_id=candidate['candidate_id'])
        plan=self.call('plan_run',step_id='evaluate',role='evaluate',attempt=1,config_id='c1',release_id='r1')
        op=self.submit(plan['plan_id'])
        internal=self.store.read('campaigns','campaign1')['operations'][op['operation_id']]
        self.assertEqual(internal['champion_model_sha256'],helpers.sha(b'{"weights":[1],"intercept":0}'))
        p=self.registry.get('p1')
        signer=BindingSigner(Ed25519PrivateKey.generate(),tracking_uri='http://localhost',role_env={},
                             ray_addresses={'trainer':'http://trainer','evaluator':'http://evaluator'})
        packet=json.loads(signer(internal,p,p.configs['c1'],p.releases['r1'])['GALATEA_EXECUTION_BINDING'])
        self.assertEqual(packet['ray_address'],'http://evaluator')
        self.assertEqual(packet['champion_model_sha256'],internal['champion_model_sha256'])

    def test_holdout_alias_cannot_reset_same_population(self):
        from galatea_mcp.projects import Registry
        from galatea_mcp.errors import DomainError
        raw=copy.deepcopy(self.registry_data)
        raw['projects'][0]['dataset']['holdout_identity']='f'*64
        with self.assertRaisesRegex(DomainError,'invalid-registry'):
            Registry(raw,helpers.FakeObjects())

    def test_stop_request_survives_watchdog_restart(self):
        op=self.submit(self.plan())
        c=self.store.read('campaigns','campaign1')
        c['operations'][op['operation_id']]['stop_requested']=True
        self.store.save('campaigns','campaign1',c)
        self.service.reconcile_all()
        self.assertEqual(self.ray.jobs[op['submission_id']]['execution'],'stopped')

    def test_repeat_attempt_does_not_change_configuration(self):
        from galatea_mcp.errors import DomainError
        op=self.submit(self.plan())
        self.ray.jobs[op['submission_id']]['execution']='failed'
        self.service.reconcile_all()
        plan=self.call('plan_run',step_id='base',role='baseline',attempt=2,config_id='c1',release_id='r1')
        second=self.submit(plan['plan_id'])
        self.assertNotEqual(op['submission_id'],second['submission_id'])
        self.assertEqual(self.call('get_campaign')['budget']['reserved_cpu_seconds'],24)
