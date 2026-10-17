"""Check App context on the real Codex wire with a local synthetic model."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from runtime_probe import mock_config, sse
from codex_agent.mcp_app_context import update_context, with_app_context
from codex_agent.rpc import AppServerClient, JsonRpcPeer
from codex_agent.state import AtomicJsonStore


@unittest.skipUnless(os.environ.get('CODEX_TEST_RUNTIME_PACKAGE'), 'native runtime opt-in required')
class McpAppRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_app_selection_is_untrusted_context_on_the_next_native_turn(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                data = sse([{'id': 'answer-' + str(len(requests)), 'type': 'message', 'role': 'assistant',
                             'content': [{'type': 'output_text', 'text': 'Synthetic App response'}]}],
                           'app-' + str(len(requests)))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory(prefix='mcp-app-runtime-') as directory:
                root = Path(directory)
                (root / 'config.toml').write_text(mock_config(server.server_port))
                process = await asyncio.create_subprocess_exec(
                    str(Path(os.environ['CODEX_TEST_RUNTIME_PACKAGE']) / 'bin/codex'),
                    'app-server', '--listen', 'stdio://', '--strict-config', cwd=root,
                    env={'PATH': '/usr/bin:/bin', 'HOME': directory, 'CODEX_HOME': directory},
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024)
                completed = asyncio.Event()

                async def notification(message):
                    if message['method'] == 'turn/completed':
                        completed.set()

                peer = JsonRpcPeer(process.stdout, process.stdin, notification=notification)
                client = AppServerClient(peer)
                try:
                    await client.initialize()
                    thread = (await client.start_thread(cwd=directory, dynamic_tools=[]))['thread']['id']
                    store = AtomicJsonStore(root / 'state')
                    scope = {'server': 'fixture', 'uri': 'ui://fixture/app', 'instanceId': 'invocation'}
                    for selection in ['app-selection-42', 'app-selection-99']:
                        completed.clear()
                        update_context(store, 'session', {**scope, 'context': {'structuredContent': {'runId': selection}}})
                        await client.start_turn(thread, 'Continue', options=with_app_context(store, 'session'))
                        await asyncio.wait_for(completed.wait(), 15)
                        matching = [item for item in requests[-1]['input'] if selection in json.dumps(item)]
                        self.assertTrue(matching)
                        self.assertTrue(all(item.get('role') == 'user' for item in matching), matching)
                        self.assertNotIn(selection, str(requests[-1].get('instructions', '')))
                    self.assertEqual(len(requests), 2)
                finally:
                    await peer.close()
                    if process.returncode is None:
                        process.terminate()
                    await process.wait()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
