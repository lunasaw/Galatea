"""Native authentication and actual Responses headers, without paid inference."""
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
from codex_agent.console.models import EventQuery, SnapshotQuery
from codex_agent.image_generation import private_write
from runtime_probe import mock_config, sse


@unittest.skipUnless(os.environ.get('CODEX_TEST_RUNTIME_PACKAGE'), 'native runtime opt-in required')
class ModelAuthRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, *, managed_key):
        expected_key = 'fixture-chat-key'
        received_auth = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.send_error(404)

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                matched = self.headers.get('Authorization') == 'Bearer ' + expected_key
                received_auth.append(matched)
                if not matched:
                    self.send_error(401, 'API_KEY_REQUIRED')
                    return
                output = sse([{'id': 'fixture-message', 'type': 'message', 'role': 'assistant',
                               'content': [{'type': 'output_text', 'text': 'Authenticated fixture.'}]}],
                             'fixture-response')
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
                package = Path(os.environ['CODEX_TEST_RUNTIME_PACKAGE'])
                (root / 'home').mkdir()
                prefix, provider = mock_config(server.server_port).split('[model_providers.mock_provider]', 1)
                _, features = provider.split('[features]', 1)
                native = (prefix.replace('"mock_provider"', '"openai"')
                          + f'openai_base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
                          + '[features]' + features)
                (root / 'home/config.toml').write_text('cli_auth_credentials_store = "file"\n' + native)
                raw = {'schema_version': 'codex-agent/v2',
                       'paths': {'codex_home': 'home', 'runtime_dir': str(package),
                                 'state_dir': 'state', 'workspace_dir': 'workspace'},
                       'codex': {'binary': str(package / 'bin/codex'), 'runtime_version': 'codex-cli 0.163.0'}}
                if managed_key:
                    raw['codex']['model_api_key_file'] = str(root / 'chat-key')
                    raw['image_generation'] = {'enabled': True, 'connection': 'custom',
                                               'base_url': 'http://127.0.0.1:1/v1', 'api_key_file': 'image-key'}
                    private_write(root / 'image-key', 'fixture-image-key')
                    private_write(root / 'home/skills/.system/imagegen/scripts/image_gen.py', '# fixture\n')
                else:
                    private_write(root / 'home/auth.json', json.dumps({'OPENAI_API_KEY': expected_key}))
                config = AgentConfig.from_dict(raw, root)
                for iteration in range(2):
                    if managed_key:
                        expected_key = f'fixture-chat-key-{iteration}'
                        private_write(root / 'chat-key', expected_key)
                    host, _ = build_host(config, development=True)
                    try:
                        await host.start()
                        account = await host.client.peer.request('account/read', {'refreshToken': False})
                        self.assertIsNotNone(account['account'], 'configured key never reached native authentication')
                        self.assertEqual(account['account']['type'], 'apiKey')
                        session_id = f'auth-{iteration}'
                        session = await host.create_session(session_id, options={'approvalPolicy': 'never'})
                        await host.send_message(session_id, f'message-{iteration}', 'Reply with fixture text.')
                        async with asyncio.timeout(20):
                            while host.get_session(session_id)['status'] != 'ready':
                                self.assertTrue(host.ready)
                                await asyncio.sleep(.02)
                        thread = (await host.client.read_thread(session['thread_id']))['thread']
                        self.assertEqual(thread['turns'][-1]['status'], 'completed', thread['turns'][-1].get('error'))
                        await host.events.flush()
                        snapshot = await host.events.snapshot(SnapshotQuery(session_id))
                        self.assertNotIn(expected_key, json.dumps(snapshot))
                        events = await host.events.read_events(EventQuery())
                        self.assertNotIn(expected_key, json.dumps(events))
                        auth_file = root / 'home/auth.json'
                        self.assertEqual(auth_file.stat().st_mode & 0o777, 0o600)
                        self.assertEqual(json.loads(auth_file.read_text())['OPENAI_API_KEY'], expected_key)
                    finally:
                        await host.stop()
                        await host.events.close()
                self.assertEqual(received_auth, [True, True])
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            worker.join(timeout=5)

    async def test_key_file_authenticates_on_start_and_restart_with_rotation(self):
        await self.exercise(managed_key=True)

    async def test_existing_native_login_is_preserved_without_managed_key(self):
        await self.exercise(managed_key=False)
