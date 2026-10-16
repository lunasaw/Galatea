"""Native conversation lifecycle against the pinned runtime and a synthetic model."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from codex_agent.app import build_host
from codex_agent.config import AgentConfig
from codex_agent.console.models import SnapshotQuery
from codex_agent.rpc import RpcError
from runtime_probe import MockModel, mock_config


@unittest.skipUnless(os.environ.get('CODEX_TEST_RUNTIME_PACKAGE'), 'native runtime opt-in required')
class ThreadLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, mode):
        with tempfile.TemporaryDirectory() as directory, MockModel('unused', 'unused', {}) as model:
            # All completions are plain synthetic text; no tools or paid inference.
            model.requests.append({'fixture': True})
            root = Path(directory)
            (root / 'home').mkdir()
            (root / 'home/config.toml').write_text(mock_config(model.server.server_port))
            package = Path(os.environ['CODEX_TEST_RUNTIME_PACKAGE'])
            config = AgentConfig.from_dict({'schema_version': 'codex-agent/v2',
                'paths': {'codex_home': 'home', 'runtime_dir': str(package), 'state_dir': 'state', 'workspace_dir': 'workspace'},
                'codex': {'binary': str(package / 'bin/codex'), 'runtime_version': 'codex-cli 0.154.0'}}, root)
            host, _ = build_host(config, development=True)
            async def send(session_id, key):
                await host.send_message(session_id, key, key)
                async with asyncio.timeout(30):
                    while host.get_session(session_id)['status'] != 'ready':
                        self.assertTrue(host.ready)
                        await asyncio.sleep(.02)
            try:
                await host.start()
                original = await host.create_session('original', options={'historyMode': mode, 'approvalPolicy': 'never', 'sandbox': 'read-only'})
                tid = original['thread_id']
                for i in range(3):
                    await send('original', f'prompt-{i}')
                thread = (await host.client.read_thread(tid))['thread']
                self.assertEqual(len(thread['turns']), 3)
                turn_ids = [turn['id'] for turn in thread['turns']]
                fork = await host.native.call('thread/fork', {'threadId': tid, 'lastTurnId': turn_ids[1], 'deferGoalContinuation': True}, 'fork-1')
                fork_session = fork['session']
                self.assertNotEqual(fork_session['thread_id'], tid)
                self.assertEqual(fork_session['settings']['approvalPolicy'], 'never')
                self.assertEqual(len((await host.client.read_thread(fork_session['thread_id']))['thread']['turns']), 2)
                await host.events.flush()
                snapshot = await host.events.snapshot(SnapshotQuery(fork_session['id']))
                self.assertEqual([m['text'] for m in snapshot['messages'] if m['role'] == 'user'], ['prompt-0', 'prompt-1'])
                method, params = ('thread/revert', {'threadId': tid, 'beforeTurnId': turn_ids[1]}) if mode == 'paginated' else ('thread/rollback', {'threadId': tid, 'numTurns': 2})
                await host.native.call(method, params, 'revert-1')
                await host.events.flush()
                snapshot = await host.events.snapshot(SnapshotQuery('original'))
                self.assertEqual([m['text'] for m in snapshot['messages'] if m['role'] == 'user'], ['prompt-0'])
                self.assertEqual(len((await host.client.read_thread(tid))['thread']['turns']), 1)
                await send('original', 'after-rollback')
                await host.native.call('thread/archive', {'threadId': tid}, 'archive-1')
                self.assertTrue(host.get_session('original')['archived'])
                await host.native.call('thread/unarchive', {'threadId': tid}, 'unarchive-1')
                self.assertFalse(host.get_session('original')['archived'])
                await send('original', 'after-unarchive')
                # Active turns cannot be destructively modified.
                session = host._session('original')
                session['status'] = 'starting'
                with self.assertRaisesRegex(ValueError, 'stop the active turn'):
                    await host.native.call('thread/delete', {'threadId': tid}, 'delete-busy')
                session['status'] = 'ready'
                if mode == 'paginated':
                    with self.assertRaisesRegex(RpcError, 'forked history still references'):
                        await host.native.call('thread/delete', {'threadId': tid}, 'delete-referenced')
                    self.assertIsNotNone(host.get_session('original'))
                    # The runtime protects referenced history; remove the synthetic fork first.
                    await host.native.call('thread/delete', {'threadId': fork_session['thread_id']}, 'delete-fork')
                await host.native.call('thread/delete', {'threadId': tid}, 'delete-1')
                self.assertIsNone(host.get_session('original'))
                self.assertNotIn('original', [s['id'] for s in host.list_sessions()])
                # Independent forks survive deleting their source.
                if mode == 'legacy':
                    self.assertIsNotNone(host.get_session(fork_session['id']))
            finally:
                await host.stop()
                await host.events.close()
            host, _ = build_host(config, development=True)
            try:
                await host.start()
                self.assertNotIn('original', [s['id'] for s in host.list_sessions()])
                if mode == 'legacy':
                    await host.resume(fork_session['id'])
                    await send(fork_session['id'], 'after-restart')
                else:
                    self.assertIsNone(host.get_session(fork_session['id']))
            finally:
                await host.stop()
                await host.events.close()

    async def test_legacy_lifecycle(self):
        await self.exercise('legacy')

    async def test_paginated_lifecycle(self):
        await self.exercise('paginated')
