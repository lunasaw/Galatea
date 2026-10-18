"""Native resource and execution contracts in a temporary HOME, no inference."""
import asyncio
import os
from pathlib import Path
import tempfile
import unittest

from codex_agent.rpc import AppServerClient, JsonRpcPeer, RpcError


@unittest.skipUnless(os.environ.get('CODEX_SETTINGS_TEST_BINARY'), 'native runtime opt-in required')
class NativeCompletionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_resources_read_revision_shell_and_execution_options(self):
        with tempfile.TemporaryDirectory(prefix='native-completion-') as directory:
            root = Path(directory)
            (root / 'config.toml').write_text('model="gpt-5.4"\nmodel_provider="fixture"\n'
                'approval_policy="never"\nsandbox_mode="read-only"\n'
                '[model_providers.fixture]\nname="Fixture"\nbase_url="http://127.0.0.1:9/v1"\n'
                'wire_api="responses"\nrequires_openai_auth=false\n')
            process = await asyncio.create_subprocess_exec(
                os.environ['CODEX_SETTINGS_TEST_BINARY'], 'app-server', '--listen', 'stdio://', '--strict-config',
                cwd=root, env={'PATH': '/usr/bin:/bin', 'HOME': directory, 'CODEX_HOME': directory},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024)
            notifications = []
            async def receive(message):
                notifications.append(message)
            peer = JsonRpcPeer(process.stdout, process.stdin, notification=receive)
            async def call(method, **params):
                return await peer.request(method, params)
            async def notified(method, **fields):
                async with asyncio.timeout(10):
                    while True:
                        result = next((n['params'] for n in notifications if n['method'] == method
                                       and all(n['params'].get(k) == v for k, v in fields.items())), None)
                        if result is not None:
                            return result
                        await asyncio.sleep(.02)
            try:
                await AppServerClient(peer).initialize()
                thread = (await call('thread/start', cwd=directory, historyMode='legacy'))['thread']['id']
                self.assertFalse(any(n['method'] == 'turn/started' for n in notifications))
                await call('thread/shellCommand', threadId=thread, command='printf native | tr a-z A-Z', timeoutMs=3000)
                await notified('turn/completed', threadId=thread)
                await call('thread/list', limit=100)
                args = dict(threadId=thread, attachmentType='reference', identityKey='issue:123')
                await call('thread/attachment/add', **args, payload={'title': 'Native resource'})
                rows = (await call('thread/attachment/list', threadId=thread))['data']
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['payload']['title'], 'Native resource')
                owners = await call('thread/attachmentOwner/list', attachmentType='reference', identityKey='issue:123')
                self.assertTrue(owners['data'])
                await call('thread/attachment/remove', **args)
                self.assertEqual((await call('thread/attachment/list', threadId=thread))['data'], [])
                revision = (await call('thread/read', threadId=thread, includeTurns=False))['readState']['revision']
                await call('thread/readState/update', threadId=thread, expectedRevision=revision, operation={'type': 'read'})
                # The runtime decides whether a no-op bumps the revision; a
                # deliberately invalid revision always conflicts.
                with self.assertRaises(RpcError):
                    await call('thread/readState/update', threadId=thread, expectedRevision='stale', operation={'type': 'unread'})
                result = await call('command/exec', command=['/bin/sh', '-c', 'printf "%s" "$NATIVE_TEST"'],
                    cwd=directory, tty=False, timeoutMs=3000, outputBytesCap=4,
                    env={'NATIVE_TEST': 'abcdef'}, sandboxPolicy={'type': 'dangerFullAccess'})
                self.assertEqual(result['exitCode'], 0)
                self.assertEqual(result['stdout'], 'abcd')
                await call('process/spawn', command=['/bin/sh', '-c', 'printf "%s" "$NATIVE_TEST"'],
                    cwd=directory, processHandle='buffered', tty=False, timeoutMs=3000,
                    outputBytesCap=4, env={'NATIVE_TEST': 'abcdef'})
                exited = await notified('process/exited', processHandle='buffered')
                self.assertEqual(exited['exitCode'], 0)
                self.assertEqual(exited['stdout'], 'abcd')
                commands = [n['params']['item'] for n in notifications if n['method'] == 'item/completed'
                            and n['params'].get('threadId') == thread and n['params']['item']['type'] == 'commandExecution']
                self.assertTrue(commands)
                self.assertIn('NATIVE', commands[-1]['aggregatedOutput'])
            finally:
                await peer.close()
                if process.returncode is None:
                    process.terminate()
                await process.wait()
