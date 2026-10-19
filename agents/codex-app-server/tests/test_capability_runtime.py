"""Pinned app-server capability contracts, isolated HOME, no model requests."""
import asyncio
import base64
import os
from pathlib import Path
import tempfile
import unittest

from codex_agent.rpc import AppServerClient, JsonRpcPeer


@unittest.skipUnless(os.environ.get('CODEX_TEST_RUNTIME_PACKAGE'), 'native runtime opt-in required')
class CapabilityRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_projects_queue_goals_files_and_pty_without_inference(self):
        with tempfile.TemporaryDirectory(prefix='native-capabilities-') as directory:
            root = Path(directory)
            (root / 'config.toml').write_text('model="gpt-5.4"\nmodel_provider="fixture"\n[model_providers.fixture]\nname="Fixture"\nbase_url="http://127.0.0.1:9/v1"\nwire_api="responses"\nrequires_openai_auth=false\n[features]\ngoals=true\n')
            binary = Path(os.environ['CODEX_TEST_RUNTIME_PACKAGE']) / 'bin/codex'
            process = await asyncio.create_subprocess_exec(
                str(binary), 'app-server', '--listen', 'stdio://', '--strict-config',
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
                other = root / 'second'
                other.mkdir()
                p = (await call('project/create', idempotencyKey='project', name='Parity',
                                roots=[{'path': directory}], metadata={'foreign': 'keep'}))['project']
                await call('project/update', projectId=p['id'], roots=[{'path': str(other)}, {'path': directory}])
                saved = (await call('project/read', projectId=p['id']))['project']
                self.assertEqual(saved['roots'][0]['path'], str(other))
                self.assertEqual(saved['metadata']['foreign'], 'keep')
                t = (await call('thread/start', cwd=directory, projectId=p['id'],
                                approvalPolicy='never', sandbox='workspace-write'))['thread']['id']
                section = (await call('threadSection/create', name='Native group'))['section']
                await call('thread/section/move', threadId=t, sectionId=section['id'])
                await call('thread/goal/set', threadId=t, objective='Paused test only', status='paused', tokenBudget=2000)
                goal = (await call('thread/goal/get', threadId=t))['goal']
                self.assertEqual((goal['status'], goal['tokenBudget']), ('paused', 2000))
                await call('thread/goal/clear', threadId=t)
                # A loaded idle thread consumes its queue automatically. Archive
                # and unarchive unload it before editing without starting a turn.
                await call('thread/archive', threadId=t)
                await call('thread/unarchive', threadId=t)
                grouped = await call('thread/list', sectionId=section['id'], sortKey='section_position',
                                     sortDirection='asc', limit=100)
                self.assertIn(t, [row['id'] for row in grouped['data']])
                unsectioned = await call('thread/list', sectionId=None, sortKey='updated_at',
                                         sortDirection='desc', limit=100)
                self.assertNotIn(t, [row['id'] for row in unsectioned['data']])
                for text in ['one', 'two']:
                    await call('thread/queue/add', threadId=t, clientUserMessageId=text,
                               input=[{'type': 'text', 'text': text, 'text_elements': []}])
                rows = (await call('thread/queue/list', threadId=t))['data']
                self.assertEqual(len(rows), 2)
                await call('thread/queue/reorder', threadId=t, queuedSubmissionIds=[row['id'] for row in reversed(rows)])
                await call('thread/queue/update', threadId=t, queuedSubmissionId=rows[0]['id'],
                           input=[{'type': 'text', 'text': 'edited', 'text_elements': []}])
                self.assertEqual((await call('thread/queue/list', threadId=t))['data'][0]['id'], rows[1]['id'])
                for row in rows:
                    await call('thread/queue/delete', threadId=t, queuedSubmissionId=row['id'])
                self.assertEqual((await call('thread/queue/list', threadId=t))['data'], [])
                file = str(other / 'file.txt')
                encoded = base64.b64encode('文本\n'.encode()).decode()
                await call('fs/writeFile', path=file, dataBase64=encoded)
                self.assertEqual((await call('fs/readFile', path=file))['dataBase64'], encoded)
                self.assertTrue((await call('fs/getMetadata', path=file))['isFile'])
                await call('fs/watch', path=str(other), watchId='watch')
                await call('fs/copy', sourcePath=file, destinationPath=file + '.copy', recursive=False)
                await notified('fs/changed', watchId='watch')
                await call('fs/unwatch', watchId='watch')
                await call('fs/remove', path=file + '.copy', recursive=False, force=False)
                await call('process/spawn', command=['/bin/cat'], cwd=directory, processHandle='pty',
                           tty=True, size={'rows': 24, 'cols': 100}, outputBytesCap=4096, timeoutMs=None)
                await call('process/resizePty', processHandle='pty', size={'rows': 30, 'cols': 120})
                await call('process/writeStdin', processHandle='pty', deltaBase64=base64.b64encode(b'pty-input\n').decode())
                output = await notified('process/outputDelta', processHandle='pty')
                self.assertIn(b'pty-input', base64.b64decode(output['deltaBase64']))
                await call('process/kill', processHandle='pty')
                await notified('process/exited', processHandle='pty')
                await call('threadSection/delete', sectionId=section['id'])
                await call('project/delete', projectId=p['id'])
                self.assertFalse(any(n['method'] == 'turn/started' for n in notifications))
            finally:
                await peer.close()
                if process.returncode is None:
                    process.terminate()
                await process.wait()
