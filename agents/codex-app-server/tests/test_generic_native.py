"""Real Codex, independently configured Skill/MCP, and a synthetic model."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from codex_agent.app import build_host
from codex_agent.config import AgentConfig
from runtime_probe import MockModel, mock_config, sse


@unittest.skipUnless(os.environ.get('CODEX_TEST_RUNTIME_PACKAGE'), 'native runtime opt-in required')
class GenericNativeTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_provider_round_trip_and_restart(self):
        with tempfile.TemporaryDirectory() as directory, MockModel('example', 'echo', {'text': 'fixture'}) as model:
            root = Path(directory)
            (root / 'home').mkdir()
            (root / 'home/config.toml').write_text(mock_config(model.server.server_port))
            package = Path(os.environ['CODEX_TEST_RUNTIME_PACKAGE'])
            config = AgentConfig.from_dict({'schema_version': 'codex-agent/v2',
                'paths': {'codex_home': 'home', 'runtime_dir': str(package), 'state_dir': 'state', 'workspace_dir': 'workspace'},
                'codex': {'binary': str(package / 'bin/codex'), 'runtime_version': 'codex-cli 0.163.0'},
                'tools': {'catalog_dir': str(ROOT / 'examples/echo'),
                          'providers': [{'path': str(ROOT / 'examples/echo/provider.py'), 'options': {'prefix': 'provider-receipt:'}}]}}, root)

            async def completed(host):
                async with asyncio.timeout(30):
                    while host.get_session('provider')['status'] != 'ready':
                        self.assertTrue(host.ready)
                        await asyncio.sleep(.01)

            host, _ = build_host(config, development=True)
            try:
                await host.start()
                await host.create_session('provider')
                await host.send_message('provider', 'first', 'Call the configured synthetic echo extension.')
                await completed(host)
                self.assertEqual(len(model.requests), 2)
                self.assertIn('provider-receipt:fixture', json.dumps(model.requests[1]['input']))
                receipt = host.registry.journal.records()[0]
                self.assertEqual(receipt['state'], 'receipt_saved')
                self.assertEqual(receipt['receipt']['data'], {'text': 'provider-receipt:fixture'})
            finally:
                await host.stop()
                await host.events.close()
            host, _ = build_host(config, development=True)
            try:
                await host.start()
                await host.resume('provider')
                await host.send_message('provider', 'second', 'Continue from the previous extension result.')
                await completed(host)
                self.assertEqual(len(model.requests), 3)
                self.assertIn('provider-receipt:fixture', json.dumps(model.requests[2]['input']))
                self.assertEqual(len(host.registry.journal.records()), 1)
            finally:
                await host.stop()
                await host.events.close()

    async def test_empty_host_discovers_skill_and_agent_calls_external_mcp(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(body)
                if len(requests) == 1:
                    matches = []
                    for tool in body.get('tools', []):
                        if tool.get('type') == 'namespace':
                            matches.extend((tool['name'], member['name']) for member in tool.get('tools', [])
                                           if 'echo' in member.get('name', ''))
                        elif 'echo' in tool.get('name', ''):
                            matches.append((None, tool['name']))
                    if len(matches) != 1:
                        items = [{'id': 'missing-tool', 'type': 'message', 'role': 'assistant',
                                  'content': [{'type': 'output_text', 'text': 'MCP echo tool missing'}]}]
                    else:
                        namespace, name = matches[0]
                        item = {'type': 'function_call', 'call_id': 'external-mcp-call', 'name': name,
                                'arguments': json.dumps({'text': 'synthetic-only'})}
                        if namespace:
                            item['namespace'] = namespace
                        items = [item]
                else:
                    items = [{'id': 'done', 'type': 'message', 'role': 'assistant',
                              'content': [{'type': 'output_text', 'text': 'External extension complete'}]}]
                output = sse(items, 'generic-' + str(len(requests)))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(output)))
                self.end_headers()
                self.wfile.write(output)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'home').mkdir()
                skill = root / 'skills/fixture/SKILL.md'
                skill.parent.mkdir(parents=True)
                skill.write_text('---\nname: standalone-fixture\ndescription: Synthetic standalone skill fixture.\n---\n\nSKILL_LOADED_SENTINEL. Use the external echo tool for the requested synthetic check.\n')
                native = mock_config(server.server_port).replace('include_instructions = false', 'include_instructions = true')
                native += '\n[mcp_servers.fixture]\ncommand = ' + json.dumps(sys.executable) + '\nargs = [' + json.dumps(str(ROOT / 'tests/fixtures/mcp_echo.py')) + ']\n'
                (root / 'home/config.toml').write_text(native)
                package = Path(os.environ['CODEX_TEST_RUNTIME_PACKAGE'])
                config = AgentConfig.from_dict({'schema_version': 'codex-agent/v2',
                    'paths': {'codex_home': 'home', 'runtime_dir': str(package), 'state_dir': 'state', 'workspace_dir': 'workspace'},
                    'codex': {'binary': str(package / 'bin/codex'), 'runtime_version': 'codex-cli 0.163.0'},
                    'skills': {'roots': ['skills']}}, root)
                host, _ = build_host(config, development=True)
                try:
                    await host.start()
                    self.assertEqual(host.registry.catalog.schemas, {})
                    skills = await host.client.peer.request('skills/list', {'cwds': [str(config.workspace_dir)], 'forceReload': True})
                    self.assertIn(str(skill), json.dumps(skills))
                    for enabled in (False, True):
                        result = await host.native.call('skills/config/write', {'path': str(skill), 'enabled': enabled},
                            'skill-' + str(enabled))
                        self.assertEqual(result['effectiveEnabled'], enabled)
                    # This request takes JSON null in the fixed 0.163.0 protocol.
                    self.assertEqual(await host.native.call('config/mcpServer/reload', None, 'reload-mcp'), {})
                    session = await host.create_session('generic', options={'approvalPolicy': 'on-request', 'sandbox': 'read-only'})
                    inventory = await host.client.peer.request('mcpServerStatus/list', {'threadId': session['thread_id']})
                    self.assertIn('echo', json.dumps(inventory))
                    await host.send_message('generic', 'request-generic', 'Use the fixture Skill and external echo.',
                        input_items=[{'type': 'text', 'text': 'Use the fixture Skill and external echo.', 'text_elements': []},
                                     {'type': 'skill', 'name': 'standalone-fixture', 'path': str(skill)}])
                    approvals = []
                    async with asyncio.timeout(30):
                        while host.get_session('generic')['status'] != 'ready':
                            for row in host.native.list_pending():
                                self.assertEqual(row['method'], 'mcpServer/elicitation/request')
                                self.assertEqual(row['params']['serverName'], 'fixture')
                                approvals.append(row['method'])
                                host.native.respond(row['id'], {'action': 'accept', 'content': {}})
                            await asyncio.sleep(.01)
                    self.assertEqual(approvals, ['mcpServer/elicitation/request'])
                    self.assertEqual(len(requests), 2, json.dumps(requests[0].get('tools')))
                    self.assertIn('SKILL_LOADED_SENTINEL', json.dumps(requests[0]))
                    self.assertIn('mcp-receipt:synthetic-only', json.dumps(requests[1]['input']))
                finally:
                    await host.stop()
                    await host.events.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
