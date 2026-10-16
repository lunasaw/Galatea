"""Native executable + synthetic Responses server; no paid inference or training."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

import test_runtime_host
from codex_agent.catalog import Catalog
from codex_agent.config import AgentConfig
from codex_agent.events import EventLog
from codex_agent.host import AgentHost
from codex_agent.process import ManagedProcess
from codex_agent.registry import ToolRegistry
from codex_agent.state import AtomicJsonStore, OperationJournal
from runtime_probe import mock_config, sse

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('GALATEA_TEST_RUNTIME_PACKAGE'), 'native runtime opt-in required')
class NativeInteractionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_shell_approval_then_question_through_host(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(body)
                index = len(requests)
                if index == 1:
                    items = [{'type': 'function_call', 'call_id': 'shell-fixture', 'name': 'exec_command',
                              'arguments': json.dumps({'cmd': 'printf native-approved', 'sandbox_permissions': 'require_escalated',
                                                       'justification': 'Synthetic approval integration test'})}]
                elif index == 2:
                    items = [{'type': 'function_call', 'call_id': 'question-fixture', 'name': 'request_user_input',
                              'arguments': json.dumps({'questions': [{'header': 'Target', 'id': 'target', 'question': 'Synthetic target?',
                                  'options': [{'label': 'One', 'description': 'First fixture'}, {'label': 'Two', 'description': 'Second fixture'}]}]})}]
                else:
                    items = [{'id': 'answer', 'type': 'message', 'role': 'assistant',
                              'content': [{'type': 'output_text', 'text': 'Native interactions complete'}]}]
                data = sse(items, 'native-' + str(index))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'codex-home').mkdir()
                config_text = mock_config(server.server_port).replace('mock-model', 'gpt-5.4').replace('shell_tool = false', 'shell_tool = true').replace('unified_exec = false', 'unified_exec = true').replace('[tools.experimental_request_user_input]\nenabled = false', '[tools.experimental_request_user_input]\nenabled = true')
                (root / 'codex-home/config.toml').write_text(config_text)
                package = Path(os.environ['GALATEA_TEST_RUNTIME_PACKAGE'])
                config = AgentConfig.from_dict({'schema_version': 'codex-agent/v1', 'paths': {
                    'codex_home': 'codex-home', 'runtime_dir': str(package), 'state_dir': 'state', 'workspace_dir': 'workspace',
                    'contracts': str(ROOT / 'config/contracts/tools.json'), 'catalog_metadata': str(ROOT / 'config/contracts/catalog-metadata.json')},
                    'codex': {'binary': str(package / 'bin/codex'), 'runtime_version': 'codex-cli 0.154.0'},
                    'galatea': {'principal': {'principal_id': 'fixture', 'actions': ['galatea_get_capabilities']}}}, root)
                events = EventLog()
                process = ManagedProcess(config, observer=events.observer)
                catalog = Catalog.load(config.contracts.parent)
                store = AtomicJsonStore(config.state_dir)
                async def start():
                    return await process.start(host.handle_tool_call, notification=host.handle_notification, on_failure=host.runtime_failed)
                host = AgentHost(registry=ToolRegistry(catalog, OperationJournal(store), {name: lambda *_: {} for name in catalog.schemas}), event_log=events, store=store,
                    client_factory=start, client_stop=process.stop, reconciler=lambda: None, principal_id='fixture',
                    project_ids=set(), campaign_ids=set(), actions={'galatea_get_capabilities'}, workspace_dir=config.workspace_dir)
                try:
                    await host.start()
                    await host.create_session('native-approval')
                    await host.send_message('native-approval', 'fixture-turn', 'Synthetic protocol test', options={
                        'collaborationMode': {'mode': 'plan', 'settings': {'model': 'gpt-5.4', 'reasoning_effort': 'medium', 'developer_instructions': None}}})
                    methods = []
                    async with asyncio.timeout(30):
                        while host.get_session('native-approval')['status'] != 'ready':
                            for row in host.native.list_pending():
                                methods.append(row['method'])
                                if row['method'] == 'item/commandExecution/requestApproval':
                                    host.native.respond(row['id'], {'decision': 'accept'})
                                elif row['method'] == 'item/tool/requestUserInput':
                                    host.native.respond(row['id'], {'answers': {'target': {'answers': ['One']}}})
                                else:
                                    self.fail('unexpected request: ' + row['method'])
                            await asyncio.sleep(.01)
                    self.assertEqual(methods, ['item/commandExecution/requestApproval', 'item/tool/requestUserInput'])
                    self.assertIn('native-approved', json.dumps(requests[1]['input']))
                    self.assertIn('One', json.dumps(requests[2]['input']))
                finally:
                    await host.stop()
                    await events.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
