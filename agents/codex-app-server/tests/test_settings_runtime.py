"""Real configuration contract, temporary HOME, no inference or user state."""
import asyncio
import os
from pathlib import Path
import tempfile
import unittest

from codex_agent.rpc import AppServerClient, JsonRpcPeer, RpcError


@unittest.skipUnless(os.environ.get('CODEX_SETTINGS_TEST_BINARY'), 'native runtime opt-in required')
class SettingsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_defaults_reset_versions_layers_and_reload(self):
        with tempfile.TemporaryDirectory(prefix='native-settings-') as directory:
            root = Path(directory)
            home = root / 'home'; home.mkdir()
            work = root / 'work'; work.mkdir()
            (work / '.git').mkdir(); (work / '.codex').mkdir()
            config_file = home / 'config.toml'
            config_file.write_text('model="gpt-5.4"\nmodel_provider="fixture"\n'
                'approval_policy="never"\nsandbox_mode="read-only"\nmodel_verbosity="high"\n'
                '[model_providers.fixture]\nname="Fixture"\nbase_url="http://127.0.0.1:9/v1"\n'
                'wire_api="responses"\nrequires_openai_auth=false\n'
                f'[projects."{work}"]\ntrust_level="trusted"\n')
            project = work / '.codex/config.toml'
            project.write_text('model_verbosity="low"\n')
            process = await asyncio.create_subprocess_exec(
                os.environ['CODEX_SETTINGS_TEST_BINARY'], 'app-server', '--listen', 'stdio://', '--strict-config',
                cwd=work, env={'PATH': '/usr/bin:/bin', 'HOME': str(home), 'CODEX_HOME': str(home)},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024)
            notifications = []
            async def receive(message):
                notifications.append(message)
            peer = JsonRpcPeer(process.stdout, process.stdin, notification=receive)
            client = AppServerClient(peer)
            async def read():
                return await peer.request('config/read', {'includeLayers': True, 'cwd': str(work)})
            async def write(edits, reload=False, version=None):
                if version is None:
                    version = next(row['version'] for row in (await read())['layers'] if row['name']['type'] == 'user')
                return await peer.request('config/batchWrite', {'filePath': str(config_file), 'expectedVersion': version,
                    'reloadUserConfig': reload, 'edits': [{'keyPath': key, 'value': value, 'mergeStrategy': 'replace'}
                                                        for key, value in edits.items()]})
            try:
                await client.initialize()
                result = await client.start_thread(cwd=str(work), dynamic_tools=[])
                self.assertEqual(result['approvalPolicy'], 'never')
                self.assertEqual(result['sandbox']['type'], 'readOnly')
                snapshot = await read()
                version = next(row['version'] for row in snapshot['layers'] if row['name']['type'] == 'user')
                await write({'model_verbosity': 'medium', 'model_reasoning_summary': 'detailed',
                             'plan_mode_reasoning_effort': 'high', 'model_context_window': 100000,
                             'model_auto_compact_token_limit': 80000})
                snapshot = await read()
                self.assertEqual(snapshot['config']['model_verbosity'], 'low')
                self.assertEqual(snapshot['config']['model_reasoning_summary'], 'detailed')
                self.assertEqual(snapshot['origins']['model_verbosity']['name']['type'], 'project')
                with self.assertRaises(RpcError):
                    await write({'web_search': 'disabled'}, version=version)
                self.assertNotEqual((await read())['config']['web_search'], 'disabled')
                with self.assertRaises(RpcError):
                    await peer.request('config/batchWrite', {'filePath': str(project), 'edits': [
                        {'keyPath': 'model_verbosity', 'value': 'high', 'mergeStrategy': 'replace'}]})
                await write({'model_verbosity': None})
                user = next(row for row in (await read())['layers'] if row['name']['type'] == 'user')
                self.assertNotIn('model_verbosity', user['config'])
                await write({'approval_policy': 'on-request', 'sandbox_mode': 'workspace-write',
                             'web_search': 'live', 'model_reasoning_summary': 'concise'}, reload=True)
                self.assertEqual((await read())['config']['web_search'], 'live')
                created = await client.start_thread(cwd=str(work), dynamic_tools=[])
                self.assertEqual(created['approvalPolicy'], 'on-request')
                self.assertEqual(created['sandbox']['type'], 'workspaceWrite')
                # Named maps and path keys use TOML quoting, and a leaf edit
                # must preserve sibling credentials that the browser hides.
                with self.assertRaises(RpcError):
                    await write({'profiles.research.model_reasoning_effort': 'high'})
                await write({'permissions."research.with.dot".description': 'Research',
                             'permissions."research.with.dot".extends': 'read-only',
                             'permissions."research.with.dot".filesystem."/private/data"': 'none',
                             'analytics.enabled': False,
                             'model_providers.fixture.env_http_headers.Authorization': 'FIXTURE_AUTH'})
                await write({'permissions."research.with.dot".description': 'Updated',
                             'model_providers.fixture.request_max_retries': 3})
                snapshot = await read()
                self.assertEqual(snapshot['config']['permissions']['research.with.dot']['description'], 'Updated')
                self.assertEqual(snapshot['config']['permissions']['research.with.dot']['filesystem']['/private/data'], 'deny')
                self.assertFalse(snapshot['config']['analytics']['enabled'])
                self.assertEqual(snapshot['config']['model_providers']['fixture']['env_http_headers']['Authorization'], 'FIXTURE_AUTH')
                # Explicit per-thread updates, unlike config reload, publish settings.
                await peer.request('thread/settings/update', {'threadId': result['thread']['id'],
                    'approvalPolicy': 'on-request', 'summary': 'concise'})
                async with asyncio.timeout(10):
                    while not any(n.get('method') == 'thread/settings/updated' and
                                  n['params']['threadSettings']['summary'] == 'concise' for n in notifications):
                        await asyncio.sleep(.02)
                self.assertFalse(any(n.get('method') == 'turn/started' for n in notifications))
            finally:
                await peer.close()
                if process.returncode is None: process.terminate()
                await process.wait()
