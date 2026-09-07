"""Cross-package contract tests over real MCP HTTP; all compute and model I/O is fake."""
from __future__ import annotations
import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
for src in [ROOT/'services/galatea-mcp/src',ROOT/'agents/training-agent-runtime/src']:
    sys.path.insert(0,str(src))
spec=importlib.util.spec_from_file_location('galatea_fixtures',ROOT/'services/galatea-mcp/tests/helpers.py')
fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)


class TrainAgentIntegrationTests(unittest.TestCase):
    def test_runner_real_http_mcp_baseline_trial_champion_evaluate_delivery(self):
        self.run_campaign()

    def test_agent_cannot_replace_verified_platform_report(self):
        self.run_campaign(forge_report=True)

    def run_campaign(self, forge_report=False):
        from galatea_mcp.state import StateStore
        from galatea_mcp.auth import Principal
        from galatea_mcp.projects import Registry
        from galatea_mcp.service import Service
        from galatea_mcp.server import create_http_app
        from training_agent.platform import SyncPlatform
        from training_agent.runner import Runner,initial_state
        from training_agent.state import Store
        from training_agent.runtime.types import TurnOutcome
        import uvicorn
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            store=StateStore(root/'platform-state')
            ray,evidence=fixtures.FakeRay(),fixtures.FakeEvidence()
            service=Service(store,Registry(fixtures.setup_registry(root),fixtures.FakeObjects()),ray,evidence,clock=lambda:100)
            service.register(fixtures.campaign())
            principal=Principal('runner',frozenset({'p1'}),frozenset({'campaign1'}),frozenset({'*'}))
            app=create_http_app(service,principal,'test-token-for-local-protocol',background=False)
            sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on',loop='asyncio',ws='none'))
            thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
            deadline=time.monotonic()+5
            while not server.started and time.monotonic()<deadline:
                time.sleep(0.01)
            self.assertTrue(server.started,'Local MCP HTTP server failed to start')
            try:
                with patch.dict(os.environ,{'TEST_GALATEA_TOKEN':'test-token-for-local-protocol'}):
                    platform=SyncPlatform({'transport':'http','url':f'http://127.0.0.1:{port}/mcp',
                                           'allow_loopback_http':True,'token_env':'TEST_GALATEA_TOKEN'})
                    self.assertEqual(len(platform.preflight()['tools']),17)
                    def call(name,**args):
                        return platform._run(lambda p:p.call('galatea_'+name,
                            {'project_id':'p1','campaign_id':'campaign1',**args}))
                    calls=[]
                    class Runtime:
                        def run_turn(self,request,save):
                            calls.append(request)
                            save('thread-real-protocol',str(request.decision_seq))
                            c,operations=platform.snapshot({'project_id':'p1','campaign_id':'campaign1'})
                            report=None
                            if c['stage']=='baseline':
                                step,role='base','baseline'
                            elif c['stage']=='search':
                                trials=[op for op in operations if op['role']=='trial']
                                if not trials:
                                    step,role='trial1','trial'
                                else:
                                    rows=call('compare_runs',run_ids=[op['run_id'] for op in operations])['ranking']
                                    selected=rows[0]
                                    call('freeze_candidate',run_id=selected['run_id'],evidence_digest=selected['evidence_digest'])
                                    step,role='champion','champion'
                            elif c['stage']=='champion_ready':
                                step,role='evaluate','evaluate'
                            else:
                                report=call('verify_candidate',candidate_id=c['candidate']['candidate_id'])
                            if report is None:
                                plan=call('plan_run',step_id=step,role=role,attempt=1,config_id='c1',release_id='r1')
                                op=call('submit_job',plan_id=plan['plan_id'],idempotency_key='request-'+str(request.decision_seq))
                                ids=[op['operation_id']]
                            else:
                                ids=[]
                            proposal={'schema_version':'galatea.agent-turn/v1','campaign_id':'campaign1',
                                'request_revision':1,'decision_seq':request.decision_seq,
                                'action':'complete' if report else 'wait_external','operation_ids':ids,
                                'evidence_refs':report['evidence_refs'] if report else [],
                                'proposed_outcome':report['outcome'] if report else None,'reason':'bounded fixture decision',
                                'user_input_request':None,'report_ref':(
                                    'invented-report' if forge_report else report['report_ref']) if report else None}
                            return TurnOutcome('thread-real-protocol',str(request.decision_seq),'completed',proposal,
                                {'thread_id':'thread-real-protocol','total_tokens':100*len(calls)})
                    runner_store=Store(root/'runner-state')
                    runner_store.save('campaign1',initial_state('p1','campaign1',1,str(root),'skill','lock','fixture lifecycle'))
                    runner=Runner(runner_store,'campaign1',platform,Runtime())
                    now=0
                    for expected_role in ['baseline','trial','champion','evaluate']:
                        runner.tick(now)
                        state=runner_store.load('campaign1')
                        self.assertEqual(state['runner_state'],'waiting_external',state)
                        _,ops=platform.snapshot(state)
                        op=next(item for item in ops if item['execution'] in {'pending','submitting','unknown','queued','running','stopping'})
                        self.assertEqual(op['role'],expected_role)
                        before=len(calls)
                        runner.tick(now+60)
                        self.assertEqual(len(calls),before,'Waiting must not invoke inference')
                        ray.jobs[op['submission_id']]['execution']='succeeded'
                        service.reconcile_all()
                        runner.tick(now+120)
                        self.assertEqual(runner_store.load('campaign1')['runner_state'],'waiting_evidence')
                        # Recreate Runner and Store from disk while publication is delayed.
                        runner_store=Store(root/'runner-state')
                        runner=Runner(runner_store,'campaign1',platform,Runtime())
                        runner.tick(now+180)
                        self.assertEqual(len(calls),before,'Evidence waiting after restart must not invoke inference')
                        fixtures.finish(service,ray,evidence,'campaign1',op['operation_id'])
                        now+=240
                    runner.tick(now)
                    final=runner_store.load('campaign1')
                    campaign,_=platform.snapshot(final)
                    report=campaign['report']
                    self.assertEqual(report['outcome'],'accepted')
                    self.assertEqual(report['integrity'],'verified')
                    self.assertEqual(report['final_test_status'],'passed')
                    if forge_report:
                        self.assertNotEqual(final['runner_state'],'completed',final)
                        self.assertEqual(final['last_error'],'Invalid proposal after platform reconciliation')
                    else:
                        self.assertEqual(final['runner_state'],'completed',final)
                        proposal=final['last_proposal']
                        self.assertEqual(proposal['proposed_outcome'],report['outcome'])
                        self.assertEqual(proposal['report_ref'],report['report_ref'])
                        self.assertEqual(proposal['evidence_refs'],report['evidence_refs'])
                    self.assertEqual(len(ray.jobs),4)
                    self.assertEqual(len(calls),5)
                    self.assertEqual({r.thread_id for r in calls[1:]},{'thread-real-protocol'})
                    self.assertEqual(final['observed_tokens'],500)
            finally:
                server.should_exit=True;thread.join(timeout=5);sock.close();store.close()
                self.assertFalse(thread.is_alive(),'Local MCP server did not shut down')
