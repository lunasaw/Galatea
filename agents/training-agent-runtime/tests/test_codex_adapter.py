import importlib.util
import json
import unittest
from types import SimpleNamespace as NS

class AdapterTests(unittest.TestCase):
    def test_identity_persisted_before_effects_and_resume_exact(self):
        self.assertIsNotNone(importlib.util.find_spec('training_agent.runtime.codex'))
        from training_agent.runtime.codex import Adapter
        from training_agent.runtime.types import TurnRequest
        events=[]
        class Handle:
            id='turn-1'
            def run(self):
                events.append('run')
                return NS(status='completed',error=None,final_response='{}',usage=NS(total=NS(total_tokens=100)))
            def interrupt(self): events.append('interrupt')
        class Thread:
            id='thread-1'
            def turn(self, inputs, **options):
                self_options=options
                assert options['output_schema']=={'type':'object'}
                assert inputs[0].name=='training-campaign'
                events.append('turn')
                return Handle()
        class Client:
            def __init__(self, config): pass
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def thread_resume(self, identity, **options):
                assert identity=='thread-1'
                assert options['approval_mode']=='deny'
                assert options['sandbox']=='workspace'
                events.append('resume')
                return Thread()
        sdk=NS(Codex=Client,CodexConfig=lambda **k:NS(**k),ApprovalMode=NS(deny_all='deny'),Sandbox=NS(workspace_write='workspace'),SkillInput=lambda **k:NS(**k),TextInput=lambda **k:NS(**k))
        request=TurnRequest('c',1,1,'/workspace','thread-1','/skills/SKILL.md','decide',{'type':'object'})
        outcome=Adapter(sdk).run_turn(request,lambda t,u:events.append((t,u)))
        self.assertEqual(events,['resume',('thread-1',None),'turn',('thread-1','turn-1'),'run'])
        self.assertEqual(outcome.usage_snapshot,{'thread_id':'thread-1','total_tokens':100})
        self.assertEqual(outcome.status,'completed')

    def test_output_rejects_cross_campaign_and_unverified_complete(self):
        self.assertIsNotNone(importlib.util.find_spec('training_agent.runtime.output'))
        from training_agent.runtime.output import validate_proposal
        proposal=dict(schema_version='galatea.agent-turn/v1',campaign_id='c',request_revision=1,decision_seq=1,action='complete',operation_ids=[],evidence_refs=[],proposed_outcome='accepted',reason='done',user_input_request=None,report_ref='r')
        state=dict(campaign_id='c',request_revision=1,decision_seq=1)
        with self.assertRaises(ValueError): validate_proposal(proposal,state,{'report':None},[])
        report=dict(report_ref='r',outcome='accepted',evidence_refs=[],integrity='verified',final_test_status='passed')
        self.assertEqual(validate_proposal(proposal,state,{'report':report},[])['action'],'complete')
        proposal['campaign_id']='other'
        with self.assertRaises(ValueError): validate_proposal(proposal,state,{'report':report},[])

    def test_adapter_uses_explicit_accepted_runtime_binary(self):
        from training_agent.runtime.codex import Adapter
        self.assertIn('codex_bin',__import__('inspect').signature(Adapter).parameters)
        configurations=[]
        class Client:
            def __init__(self,config): configurations.append(config); raise RuntimeError('No model')
        sdk=NS(Codex=Client,CodexConfig=lambda **k:NS(**k))
        from training_agent.runtime.types import TurnRequest
        outcome=Adapter(sdk,codex_bin='/accepted/codex').run_turn(TurnRequest('c',1,1,'/w',None,'/s','p',{}),lambda *_:None)
        self.assertEqual(configurations[0].codex_bin,'/accepted/codex')
        self.assertEqual(outcome.status,'failed')
