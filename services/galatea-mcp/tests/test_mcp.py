import asyncio
import json
import unittest
from helpers import FakeRay, FakeEvidence
import helpers


class MCPTests(helpers.ServiceFixture):
    def test_real_protocol_handshake_list_and_dual_error_envelope(self):
        from galatea_mcp.server import make_server
        from mcp.shared.memory import create_client_server_memory_streams
        from mcp import ClientSession
        async def run():
            server = make_server(self.service, self.principal)
            async with create_client_server_memory_streams() as (client_streams, server_streams):
                task = asyncio.create_task(server.run(*server_streams, server.create_initialization_options()))
                try:
                    async with ClientSession(*client_streams) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        self.assertEqual(len(tools.tools), 17)
                        good = await session.call_tool('galatea_get_campaign', {'project_id':'p1','campaign_id':'campaign1'})
                        self.assertFalse(good.isError)
                        self.assertEqual(json.loads(good.content[0].text), good.structuredContent)
                        bad = await session.call_tool('galatea_plan_run', {'approved':True})
                        self.assertTrue(bad.isError)
                        self.assertEqual(bad.structuredContent['error']['category'], 'invalid-input')
                        self.assertEqual(json.loads(bad.content[0].text),bad.structuredContent)
                        unknown = await session.call_tool('galatea_promote_model', {})
                        self.assertTrue(unknown.isError)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        asyncio.run(run())

    def test_http_auth_rejects_missing_or_wrong_token(self):
        from galatea_mcp.server import create_http_app
        import httpx
        async def run():
            app = create_http_app(self.service, self.principal, 'secret-test-token', background=False)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as client:
                for headers in [{}, {'Authorization':'Bearer wrong'}]:
                    response=await client.post('/mcp',headers=headers,json={})
                    self.assertEqual(response.status_code,401)
                    self.assertNotIn('secret-test-token',response.text)
        asyncio.run(run())
