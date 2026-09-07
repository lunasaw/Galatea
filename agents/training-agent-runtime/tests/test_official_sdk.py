"""Optional source-level characterization. No alternate app-server protocol or model calls."""
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

SOURCE=os.environ.get('CODEX_SDK_SOURCE')

@unittest.skipUnless(SOURCE,'Set CODEX_SDK_SOURCE to an inspected official SDK src directory')
class OfficialSDKTests(unittest.TestCase):
    def test_actual_sdk_start_resume_collector_success_and_failure_semantics(self):
        sys.path.insert(0,SOURCE)
        import openai_codex as sdk
        from openai_codex import api
        from openai_codex.models import InitializeResponse,Notification
        from openai_codex.generated.v2_all import ItemCompletedNotification,TurnCompletedNotification,ThreadTokenUsageUpdatedNotification
        from training_agent.runtime.codex import Adapter
        from training_agent.runtime.types import TurnRequest
        events=[]
        class Client:
            def __init__(self,config=None): pass
            def start(self): pass
            def close(self): pass
            def initialize(self): return InitializeResponse(userAgent='codex/0.147.0')
            def thread_start(self,params):
                self_test.assertFalse(params.ephemeral)
                self_test.assertEqual(params.approval_policy.root.value,'never')
                return NS(thread=NS(id='thread'))
            def thread_resume(self,identity,params):
                self_test.assertEqual(identity,'thread')
                return NS(thread=NS(id='thread'))
            def turn_start(self,identity,inputs,params):
                events.append('effect')
                self_test.assertEqual(inputs[0]['type'],'skill')
                self_test.assertEqual(params.output_schema,{'type':'object'})
                return NS(turn=NS(id='turn'))
        self_test=self
        usage=dict(cachedInputTokens=0,inputTokens=60,outputTokens=40,reasoningOutputTokens=0,totalTokens=100)
        for status,text,completion in [('completed','{}',True),('failed','{}',True),('interrupted','{}',True),('completed','',True),('completed','not JSON',True),('completed','{}',False)]:
            with self.subTest(status=status,text=text,completion=completion):
                events.clear()
                def stream(_):
                    yield Notification('item/completed',ItemCompletedNotification.model_validate(dict(threadId='thread',turnId='turn',completedAtMs=0,item=dict(id='item',type='agentMessage',phase='final_answer',text=text))))
                    yield Notification('thread/tokenUsage/updated',ThreadTokenUsageUpdatedNotification.model_validate(dict(threadId='thread',turnId='turn',tokenUsage=dict(total=usage,last=usage))))
                    if completion:
                        yield Notification('turn/completed',TurnCompletedNotification.model_validate(dict(threadId='thread',turn=dict(id='turn',items=[],status=status))))
                with patch.object(api,'CodexClient',Client),patch.object(api.TurnHandle,'stream',stream):
                    request=TurnRequest('c',1,1,'/work',None,'/skills/SKILL.md','request',{'type':'object'})
                    outcome=Adapter(sdk).run_turn(request,lambda t,u:events.append((t,u)))
                    self.assertEqual(outcome.status,'completed' if status=='completed' and text=='{}' and completion else 'failed')
                    self.assertEqual(events[:3],[('thread',None),'effect',('thread','turn')])
                    request.thread_id='thread'
                    resumed=Adapter(sdk).run_turn(request,lambda *_:None)
                    self.assertEqual(resumed.status,outcome.status)
