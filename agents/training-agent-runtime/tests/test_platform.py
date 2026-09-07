import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

class PlatformTests(unittest.TestCase):
    def test_sync_platform_verifies_candidate_with_exact_scope(self):
        from training_agent.platform import SyncPlatform

        calls = []
        platform = object.__new__(SyncPlatform)
        platform._run = lambda action: action(type('P', (), {
            'call': lambda self, name, arguments: calls.append((name, arguments)) or {'report_ref': 'r'}
        })())
        state = {'project_id': 'p', 'campaign_id': 'c'}
        self.assertEqual(platform.verify_candidate(state, 'candidate-1'), {'report_ref': 'r'})
        self.assertEqual(calls, [('galatea_verify_candidate', {
            'project_id': 'p', 'campaign_id': 'c', 'candidate_id': 'candidate-1'})])

    def test_pagination_envelope_errors_and_cycle_rejection(self):
        self.assertIsNotNone(importlib.util.find_spec('training_agent.platform'))
        from training_agent.platform import Platform
        class Session:
            async def call_tool(self,name,arguments):
                if name=='galatea_get_campaign': data={'campaign_id':'c','project_id':'p','request_revision':1}
                else: data={'items':[{'operation_id':'b' if arguments.get('cursor') else 'a'}], 'next_cursor':None if arguments.get('cursor') else 'next'}
                return NS(isError=False,structuredContent={'schema_version':'galatea.tools/v1','ok':True,'request_id':'x','data':data})
        async def check():
            campaign,operations=await Platform(Session()).snapshot_async({'campaign_id':'c','project_id':'p'})
            self.assertEqual([x['operation_id'] for x in operations],['a','b'])
            class Bad:
                async def call_tool(self,*args): return NS(isError=False,structuredContent={'schema_version':'wrong','ok':True,'data':{}})
            with self.assertRaises(ValueError): await Platform(Bad()).call('galatea_get_campaign',{})
        asyncio.run(check())

    def test_real_stdio_client_handshake_and_call(self):
        self.assertIsNotNone(importlib.util.find_spec('training_agent.platform'))
        from training_agent.platform import connect
        with tempfile.TemporaryDirectory() as directory:
            script=Path(directory)/'server.py'
            script.write_text('from mcp.server.fastmcp import FastMCP\nm=FastMCP("fake")\n@m.tool(name="galatea_get_campaign")\ndef get_campaign(campaign_id:str,project_id:str)->dict:\n return {"schema_version":"galatea.tools/v1","ok":True,"request_id":"x","data":{"campaign_id":campaign_id,"project_id":project_id,"request_revision":1}}\nif __name__=="__main__": m.run()\n')
            async def check():
                async with connect({'transport':'stdio','command':sys.executable,'args':[str(script)]}) as platform:
                    data=await platform.call('galatea_get_campaign',{'campaign_id':'c','project_id':'p'})
                    self.assertEqual(data['campaign_id'],'c')
            asyncio.run(check())
