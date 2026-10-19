import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

import test_host_core
from test_console import call, payload
from codex_agent.console.server import ConsoleApp
from codex_agent.console.models import EventQuery, SnapshotQuery
from codex_agent.console.adapters.sqlite import SqliteConsoleObservationStore
from codex_agent.console.service import ConsoleObservationService
from codex_agent.native import PROTOCOL
from codex_agent.rpc import RpcError


class NativeWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_host_core.HostTests.asyncSetUp
    asyncTearDown = test_host_core.HostTests.asyncTearDown

    async def test_verification_preserves_public_identity_and_challenge_proof(self):
        self.client.peer = type('Peer', (), {'healthy': True})()
        self.client.peer.request = AsyncMock(return_value={'credentialId': None, 'unavailableReason': None})
        status = await self.host.native.call('userVerification/status', {}, 'verification-status')
        self.assertIsNone(status['credentialId'])
        self.client.peer.request.return_value = {'proof': {'credentialId': 'public-key-id', 'signature': 'challenge-signature'}}
        result = await self.host.native.call('userVerification/verify', {'challenge': 'YWJj', 'title': 'Test', 'description': ''}, 'verification-proof')
        self.assertEqual(result['proof'], {'credentialId': 'public-key-id', 'signature': 'challenge-signature'})

    async def test_switching_permission_profiles_does_not_keep_an_obsolete_sandbox(self):
        await self.host.create_session('settings-chat')
        session = self.host._sessions['settings-chat']
        self.client.peer = type('Peer', (), {'healthy': True})()
        self.client.peer.request = AsyncMock(return_value={})
        policy = {'type': 'workspaceWrite', 'writableRoots': ['/fixture/output'], 'networkAccess': False,
                  'excludeTmpdirEnvVar': True, 'excludeSlashTmp': True}
        await self.host.native.call('thread/settings/update', {'threadId': session['thread_id'], 'sandboxPolicy': policy}, 'sandbox')
        await self.host.native.call('thread/settings/update', {'threadId': session['thread_id'], 'permissions': 'read-only'}, 'profile')
        self.assertNotIn('sandboxPolicy', session['settings'])
        self.assertEqual(session['settings']['permissions'], 'read-only')
        await self.host.native.call('thread/settings/update', {'threadId': session['thread_id'], 'sandboxPolicy': policy}, 'sandbox-again')
        self.assertNotIn('permissions', session['settings'])
        self.assertEqual(session['settings']['sandboxPolicy'], policy)

    async def test_client_integrations_do_not_queue_human_credential_requests(self):
        for method in ('attestation/generate', 'account/chatgptAuthTokens/refresh'):
            response = await self.host.native.request({'id': 'client-integration', 'method': method, 'params': {}})
            self.assertEqual(response['error']['code'], -32601)
            self.assertEqual(self.host.native.list_pending(), [])

    async def test_approval_round_trip_validation_duplicate_and_stale_response(self):
        await self.host.create_session('approval-chat')
        task = asyncio.create_task(self.host.handle_tool_call({'id': 'native-1', 'method': 'item/commandExecution/requestApproval',
            'params': {'threadId': 'thread-1', 'turnId': 'turn-1', 'itemId': 'command-1', 'command': 'touch output',
                       'availableDecisions': ['accept', 'decline']}}))
        await asyncio.sleep(0)
        row = self.host.native.list_pending()[0]
        self.assertEqual(row['session_id'], 'approval-chat')
        with self.assertRaises(ValueError):
            self.host.native.respond(row['id'], {'decision': 'wrong'})
        result = {'decision': 'accept'}
        self.host.native.respond(row['id'], result)
        self.assertEqual(await task, {'result': result})
        self.assertEqual(self.host.native.respond(row['id'], result)['status'], 'submitted')
        with self.assertRaises(ValueError):
            self.host.native.respond(row['id'], {'decision': 'decline'})
        self.assertEqual(self.host.native.list_pending(), [])

    async def test_question_permissions_mcp_and_legacy_responses(self):
        scenarios = [
            ('item/tool/requestUserInput', {'questions': [{'id': 'q', 'question': 'Where?'}]}, {'answers': {'q': {'answers': ['here']}}}),
            ('item/permissions/requestApproval', {}, {'permissions': {}, 'scope': 'turn'}),
            ('mcpServer/elicitation/request', {'mode': 'form'}, {'action': 'decline', 'content': None, '_meta': None}),
            ('execCommandApproval', {}, {'decision': 'approved'}),
            ('applyPatchApproval', {}, {'decision': {'denied': {'rejection': 'User declined'}}}),
        ]
        for method, params, response in scenarios:
            with self.subTest(method=method):
                task = asyncio.create_task(self.host.handle_tool_call({'id': method, 'method': method, 'params': params}))
                await asyncio.sleep(0)
                self.host.native.respond(self.host.native.list_pending()[0]['id'], response)
                self.assertEqual(await task, {'result': response})

    async def test_runtime_failure_cancels_requests_and_never_replays_approval(self):
        task = asyncio.create_task(self.host.handle_tool_call({'id': 3, 'method': 'item/fileChange/requestApproval', 'params': {}}))
        await asyncio.sleep(0)
        key = self.host.native.list_pending()[0]['id']
        await self.host.runtime_failed(None)
        self.assertIn('error', await task)
        with self.assertRaises(ValueError):
            self.host.native.respond(key, {'decision': 'accept'})

    async def test_native_http_long_call_does_not_block_approval_and_is_idempotent(self):
        entered = asyncio.Event()
        calls = []
        async def request(method, params, **kwargs):
            calls.append(method)
            entered.set()
            return await self.host.native.request({'id': 9, 'method': 'mcpServer/elicitation/request',
                'params': {'threadId': 'subagent-1', 'mode': 'form', 'message': 'Allow?', 'requestedSchema': {}}})
        self.client.peer = type('Peer', (), {'healthy': True})()
        self.client.peer.request = request
        app = ConsoleApp(self.host, self.events)
        args = {'method': 'mcpServer/tool/call', 'params': {'server': 'fixture', 'tool': 'fixture'}}
        # Runtime schema is authoritative, including fields changing between versions.
        ref = PROTOCOL.schema(args['method'])['$ref'].split('/')[-1]
        fields = PROTOCOL.bundle['client']['definitions'][ref]['properties']
        args['params'] = {key: 'fixture' for key in PROTOCOL.bundle['client']['definitions'][ref].get('required', [])}
        for key in ['arguments']:
            if key in fields:
                args['params'][key] = {}
        task = asyncio.create_task(call(app, 'POST', '/api/native/call', args, {'idempotency-key': 'long-native'}))
        await asyncio.wait_for(entered.wait(), 1)
        pending = payload(await call(app, 'GET', '/api/native/requests'))
        self.assertIsNone(pending[0]['session_id'])
        response = await call(app, 'POST', '/api/native/requests/' + pending[0]['id'],
                             {'result': {'action': 'decline', 'content': None, '_meta': None}})
        self.assertEqual(response[0]['status'], 200)
        self.assertEqual((await task)[0]['status'], 200)
        again = await call(app, 'POST', '/api/native/call', args, {'idempotency-key': 'long-native'})
        self.assertEqual(again[0]['status'], 200)
        self.assertEqual(len(calls), 1)

    async def test_schema_and_native_errors_do_not_break_runtime(self):
        self.client.peer = type('Peer', (), {'healthy': True})()
        self.client.peer.request = AsyncMock(side_effect=RpcError({'code': -32602, 'message': 'feature unavailable'}))
        app = ConsoleApp(self.host, self.events)
        sent = await call(app, 'POST', '/api/native/call', {'method': 'fs/readFile', 'params': {}}, {'idempotency-key': 'invalid'})
        self.assertEqual(sent[0]['status'], 409)
        self.assertFalse(self.client.peer.request.called)
        sent = await call(app, 'POST', '/api/native/call', {'method': 'model/list', 'params': {}}, {'idempotency-key': 'error'})
        self.assertEqual(sent[0]['status'], 422)
        self.assertIn('feature unavailable', payload(sent)['error'])
        self.assertTrue(self.host.ready)

    async def test_multimodal_send_preserves_native_input_and_deduplicates(self):
        await self.host.create_session('multimodal')
        self.client.start_turn = AsyncMock(return_value={'turn': {'id': 'turn-image'}})
        items = [{'type': 'image', 'url': 'data:image/png;base64,aGVsbG8='}, {'type': 'skill', 'name': 'fixture', 'path': '/workspace/SKILL.md'}]
        await self.host.send_message('multimodal', 'image-request', '', input_items=items, options={'effort': 'high'})
        await self.host.send_message('multimodal', 'image-request', '', input_items=items, options={'effort': 'high'})
        self.client.start_turn.assert_awaited_once_with('thread-1', '', input_items=items, options={'effort': 'high'})
        with self.assertRaises(ValueError):
            await self.host.send_message('multimodal', 'override', 'x', options={'threadId': 'other'})

    async def test_mcp_reload_requires_null_through_http_bridge(self):
        self.client.peer = type('Peer', (), {'healthy': True})()
        self.client.peer.request = AsyncMock(return_value={})
        app = ConsoleApp(self.host, self.events)
        response = await call(app, 'POST', '/api/native/call',
            {'method': 'config/mcpServer/reload', 'params': None}, {'idempotency-key': 'reload-null'})
        self.assertEqual(response[0]['status'], 200)
        self.client.peer.request.assert_awaited_once_with('config/mcpServer/reload', None, timeout=60)
        response = await call(app, 'POST', '/api/native/call',
            {'method': 'config/mcpServer/reload', 'params': {}}, {'idempotency-key': 'reload-object'})
        self.assertEqual(response[0]['status'], 409)
        self.assertEqual(self.client.peer.request.await_count, 1)

    async def test_plugin_reconcile_obeys_release_immutability_and_updates_dev_binding(self):
        self.client.peer = type('Peer', (), {'healthy': True})()
        self.client.peer.request = AsyncMock(return_value={
            'changedPlugins': [], 'failedRemotePluginIds': [], 'failedMaterializationRemotePluginIds': []})
        self.host.configuration_changed = None
        with self.assertRaisesRegex(ValueError, 'immutable release'):
            await self.host.native.call('plugin/reconcile', {'reason': 'user'}, 'immutable-reconcile')
        self.client.peer.request.assert_not_awaited()
        self.host.configuration_changed = AsyncMock()
        await self.host.native.call('plugin/reconcile', {'reason': 'user'}, 'dev-reconcile')
        self.client.peer.request.assert_awaited_once()
        self.host.configuration_changed.assert_awaited_once()

    async def test_import_running_native_thread_tracks_lifecycle_without_replaying_turn(self):
        self.client.peer = type('Peer', (), {'healthy': True})()
        self.client.peer.request = AsyncMock(return_value={'thread': {'id': 'child-thread', 'cwd': '/fixture',
            'turns': [{'id': 'child-turn', 'status': 'inProgress', 'items': []}]}})
        result = await self.host.native.call('thread/resume', {'threadId': 'child-thread'}, 'import-child')
        session = result['session']
        self.assertEqual(session['status'], 'running')
        self.assertEqual(self.host._active_turns, 1)
        await self.host.handle_notification({'method': 'turn/completed', 'params': {'threadId': 'child-thread',
            'turn': {'id': 'child-turn', 'status': 'completed'}}})
        self.assertEqual(self.host.get_session(session['id'])['status'], 'ready')
        self.assertEqual(self.host._active_turns, 0)

    async def test_restore_is_ordered_and_replaces_only_target_chat(self):
        await self.host.create_session('restore')
        self.events.append('other', {'kind': 'message_accepted', 'host_request_id': 'other', 'public': {'text': 'keep'}})
        thread = {'id': 'thread-1', 'turns': [{'id': 't', 'status': 'completed', 'items': [
            {'id': 'z-user', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'question'}]},
            {'id': 'a-answer', 'type': 'agentMessage', 'text': 'answer'}]}]}
        self.host.restore_thread('restore', thread)
        self.host.restore_thread('restore', thread)
        await self.events.flush()
        snapshot = await self.events.snapshot(SnapshotQuery('restore'))
        self.assertEqual([m['text'] for m in snapshot['messages']], ['question', 'answer'])
        self.assertEqual(len((await self.events.snapshot(SnapshotQuery('other')))['messages']), 1)


class DurableConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_recovers_redacted_events_cursor_and_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'console.db'
            first = ConsoleObservationService(SqliteConsoleObservationStore(path))
            await first.start()
            first.append('s', {'kind': 'message_accepted', 'host_request_id': 'r', 'public': {'text': 'durable', 'token': 'must-not-store'}})
            await first.flush()
            before = await first.snapshot(SnapshotQuery('s'))
            await first.close()
            self.assertNotIn(b'must-not-store', path.read_bytes())
            second = ConsoleObservationService(SqliteConsoleObservationStore(path))
            await second.start()
            self.assertEqual((await second.snapshot(SnapshotQuery('s')))['messages'], before['messages'])
            self.assertEqual(second.epoch, before['epoch'])
            second.append('s', {'kind': 'message_accepted', 'host_request_id': 'r2', 'public': {'text': 'next'}})
            await second.flush()
            self.assertEqual(len((await second.read_events(EventQuery(after=before['watermark'])))['events']), 1)
            self.assertEqual((await second.status())['storage']['backend'], 'sqlite')
            await second.close()


class NativeCapabilityLifecycleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_host_core.HostTests.asyncSetUp
    asyncTearDown = test_host_core.HostTests.asyncTearDown

    async def prepare(self):
        session = await self.host.create_session('capability')
        self.client.peer = type('Peer', (), {'healthy': True})()
        self.client.peer.request = AsyncMock(return_value={})
        return self.host._session_for_thread(session['thread_id'])

    async def test_current_turn_settings_never_change_future_defaults_or_following_turn(self):
        session = await self.prepare()
        session.update(status='running', active_turn_id='current', settings={'model': 'default'})
        await self.host.native.call('turn/settings/update', {'threadId': 'thread-1', 'turnId': 'current', 'model': 'live'}, 'live-settings')
        self.assertEqual(session['settings'], {'model': 'default'})
        self.assertEqual(session['turn_settings'], {'model': 'live'})
        await self.host.handle_notification({'method': 'turn/completed', 'params': {'threadId': 'thread-1', 'turn': {'id': 'current', 'status': 'completed'}}})
        self.assertNotIn('turn_settings', session)
        with self.assertRaisesRegex(ValueError, 'turn identity'):
            await self.host.native.call('turn/settings/update', {'threadId': 'thread-1', 'turnId': 'current', 'model': 'stale'}, 'stale-settings')
        self.assertEqual(self.client.peer.request.await_count, 1)

    async def test_queue_start_reserves_once_and_handles_notification_before_response(self):
        session = await self.prepare()
        async def start(method, params, **kwargs):
            self.assertEqual(session['status'], 'starting')
            self.assertEqual(self.host._active_turns, 1)
            for method, status in [('turn/started', 'inProgress'), ('turn/completed', 'completed')]:
                await self.host.handle_notification({'method': method, 'params': {'threadId': 'thread-1', 'turn': {'id': 'queued', 'status': status}}})
            return {'turn': {'id': 'queued'}}
        self.client.peer.request.side_effect = start
        await self.host.native.call('thread/queue/start', {'threadId': 'thread-1', 'queuedSubmissionId': 'q'}, 'queue-start')
        self.assertEqual(session['status'], 'ready')
        self.assertEqual(self.host._active_turns, 0)
        self.assertIsNone(session['active_turn_id'])

    async def test_queue_rejection_releases_reservation_and_limit_prevents_dispatch(self):
        session = await self.prepare()
        self.client.peer.request.side_effect = RpcError({'code': -32602, 'message': 'queue item missing'})
        with self.assertRaises(RpcError):
            await self.host.native.call('thread/queue/start', {'threadId': 'thread-1', 'queuedSubmissionId': 'gone'}, 'queue-missing')
        self.assertEqual(session['status'], 'ready')
        self.assertEqual(self.host._active_turns, 0)
        self.host._active_turns = self.host.max_active_turns
        with self.assertRaisesRegex(ValueError, 'active turn limit'):
            await self.host.native.call('thread/queue/start', {'threadId': 'thread-1', 'queuedSubmissionId': 'next'}, 'queue-limit')
        self.assertEqual(self.client.peer.request.await_count, 1)

    async def test_import_history_is_readable_on_immutable_host(self):
        await self.prepare()
        self.host.configuration_changed = None
        await self.host.native.call('externalAgentConfig/import/readHistories', None, 'read-history')
        self.client.peer.request.assert_awaited_once()

    async def test_project_groups_are_shared_revisioned_and_retry_safe(self):
        app = ConsoleApp(self.host, self.events)
        initial = payload(await call(app, 'GET', '/api/sidebar-groups'))
        self.assertEqual(initial, {'sections': [], 'revision': 0})
        body = {'sections': [{'id': 'group', 'name': '项目分组'}], 'revision': 0}
        result = await call(app, 'POST', '/api/sidebar-groups', body, {'idempotency-key': 'groups'})
        self.assertEqual(result[0]['status'], 200)
        again = await call(app, 'POST', '/api/sidebar-groups', body, {'idempotency-key': 'groups'})
        self.assertEqual(payload(again), payload(result))
        other = ConsoleApp(self.host, self.events)
        self.assertEqual(payload(await call(other, 'GET', '/api/sidebar-groups')), {'sections': body['sections'], 'revision': 1})
        stale = await call(other, 'POST', '/api/sidebar-groups', {'sections': [], 'revision': 0}, {'idempotency-key': 'stale-groups'})
        self.assertEqual(stale[0]['status'], 409)
