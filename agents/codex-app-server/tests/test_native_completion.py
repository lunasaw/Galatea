"""Admission, event races, settings and resources added by native parity work."""
import asyncio
import unittest
from unittest.mock import AsyncMock

import test_host_core
from test_console import call, payload
from codex_agent.console.server import ConsoleApp
from codex_agent.rpc import RpcError
from codex_agent.console.projection import redact
from unittest.mock import patch


class NativeCompletionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_host_core.HostTests.asyncSetUp
    asyncTearDown = test_host_core.HostTests.asyncTearDown

    async def prepare(self):
        await self.host.create_session('source')
        self.client.peer = type('Peer', (), {'healthy': True})()
        self.client.peer.request = AsyncMock(return_value={})
        return self.host._sessions['source']

    async def test_shell_ack_keeps_slot_until_native_completion_and_allows_auxiliary_command(self):
        session = await self.prepare()
        await self.host.native.call('thread/shellCommand', {'threadId': 'thread-1', 'command': 'echo fixture'}, 'shell')
        self.assertEqual(session['status'], 'starting')
        self.assertEqual(self.host._count_active_turns(), 1)
        await self.host.handle_notification({'method': 'turn/started', 'params': {'threadId': 'thread-1', 'turn': {'id': 'shell-turn'}}})
        await self.host.native.call('thread/shellCommand', {'threadId': 'thread-1', 'command': 'echo auxiliary'}, 'auxiliary')
        self.assertEqual(session['active_request_id'], 'shell')
        self.assertEqual(self.host._active_turns, 1)
        await self.host.handle_notification({'method': 'turn/completed', 'params': {'threadId': 'thread-1', 'turn': {'id': 'shell-turn', 'status': 'completed'}}})
        self.assertEqual(session['status'], 'ready')
        self.assertEqual(self.host._active_turns, 0)
        self.assertEqual(self.store.read('requests/shell.json')['status'], 'completed')

    async def test_shell_early_events_do_not_resurrect_turn_and_native_rejection_releases_slot(self):
        session = await self.prepare()
        async def early(*args, **kwargs):
            for method, status in [('turn/started', 'inProgress'), ('turn/completed', 'completed')]:
                await self.host.handle_notification({'method': method, 'params': {'threadId': 'thread-1', 'turn': {'id': 'early', 'status': status}}})
            return {}
        self.client.peer.request.side_effect = early
        await self.host.native.call('thread/shellCommand', {'threadId': 'thread-1', 'command': 'true'}, 'early-shell')
        self.assertEqual(session['status'], 'ready')
        self.assertEqual(self.host._active_turns, 0)
        self.client.peer.request.side_effect = RpcError({'code': -32602, 'message': 'disabled'})
        with self.assertRaises(RpcError):
            await self.host.native.call('thread/shellCommand', {'threadId': 'thread-1', 'command': 'true'}, 'rejected-shell')
        self.assertEqual(session['status'], 'ready')
        self.assertEqual(self.host._active_turns, 0)

    async def test_shell_uncertain_transport_retains_admission_reservation(self):
        session = await self.prepare()
        self.client.peer.request.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            await self.host.native.call('thread/shellCommand', {'threadId': 'thread-1', 'command': 'true'}, 'uncertain-shell')
        self.assertEqual(session['status'], 'recovery_required')
        self.assertEqual(self.host._count_active_turns(), 1)

    async def test_detached_review_binds_child_and_completion_before_response_is_reconciled(self):
        session = await self.prepare()
        self.client.peer.request.return_value = {'turn': {'id': 'review-turn'}, 'reviewThreadId': 'child'}
        self.client.read_thread = AsyncMock(return_value={'thread': {'id': 'child', 'turns': [{'id': 'review-turn', 'status': 'completed', 'items': []}]}})
        result = await self.host.native.call('review/start', {'threadId': 'thread-1', 'target': {'type': 'uncommittedChanges'}, 'delivery': 'detached'}, 'review')
        self.assertEqual(session['status'], 'ready')
        self.assertEqual(result['session']['thread_id'], 'child')
        self.assertEqual(result['session']['status'], 'ready')
        self.assertIn(result['session']['id'], self.host._attached)
        self.assertEqual(self.host._active_turns, 0)
        self.assertEqual(self.store.read('requests/review.json')['status'], 'completed')

    async def test_detached_review_completion_during_read_wins_over_stale_transcript(self):
        await self.prepare()
        self.client.peer.request.return_value = {'turn': {'id': 'review-turn'}, 'reviewThreadId': 'child'}
        async def stale(thread_id):
            await self.host.handle_notification({'method': 'turn/completed', 'params': {'threadId': 'child', 'turn': {'id': 'review-turn', 'status': 'completed'}}})
            return {'thread': {'id': 'child', 'turns': [{'id': 'review-turn', 'status': 'inProgress', 'items': []}]}}
        self.client.read_thread = stale
        result = await self.host.native.call('review/start', {'threadId': 'thread-1', 'target': {'type': 'custom', 'instructions': 'check'}, 'delivery': 'detached'}, 'race-review')
        self.assertEqual(result['session']['status'], 'ready')
        self.assertIsNone(result['session']['active_turn_id'])
        self.assertEqual(self.host._active_turns, 0)

    async def test_resource_notifications_persist_without_changing_admission_status(self):
        session = await self.prepare()
        self.host._attached.discard(session['id'])
        for method, extra in [
            ('thread/readState/changed', {'readState': {'firstUnread': None, 'revision': 'v2'}}),
            ('thread/project/updated', {'projectId': 'project-native'}),
            ('thread/status/changed', {'status': {'type': 'idle'}}),
            ('thread/prediction/updated', {'prediction': 'next'}),
            ('thread/environment/disconnected', {'reason': 'ended'}),
            ('thread/name/updated', {'threadName': 'Renamed by native client'}),
        ]:
            await self.host.handle_notification({'method': method, 'params': {'threadId': 'thread-1', **extra}})
        persisted = self.store.read('sessions/source.json')
        self.assertEqual(persisted['status'], 'ready')
        self.assertEqual(persisted['read_state']['revision'], 'v2')
        self.assertEqual(persisted['settings']['projectId'], 'project-native')
        self.assertEqual(persisted['prediction']['prediction'], 'next')
        self.assertFalse(persisted['environment_status']['connected'])
        self.assertEqual(persisted['name'], 'Renamed by native client')

    async def test_full_config_schema_is_shipped_with_named_permissions(self):
        response = await call(ConsoleApp(self.host, self.events), 'GET', '/api/native/config-schema')
        self.assertEqual(response[0]['status'], 200)
        schema = payload(response)
        self.assertEqual(len(schema['properties']), 103)
        self.assertEqual(schema['definitions']['PermissionsToml']['additionalProperties']['$ref'], '#/definitions/PermissionProfileToml')
        self.assertEqual(schema['x-source']['revision'][:7], 'e89b7c4')

    async def test_config_read_preserves_sources_and_storage_modes_without_exposing_credentials(self):
        await self.prepare()
        origin = {'name': {'type': 'user', 'file': '/fixture/config.toml'}, 'version': 'v1'}
        config = {'cli_auth_credentials_store': 'file', 'api_key': 'fixture-private-key'}
        self.client.peer.request.return_value = {'config': config,
            'origins': {'cli_auth_credentials_store': origin, 'api_key': origin},
            'layers': [{'name': origin['name'], 'version': 'v1', 'config': config}]}
        result = await self.host.native.call('config/read', {'includeLayers': True}, None)
        self.assertEqual(result['origins'], self.client.peer.request.return_value['origins'])
        for values in (result['config'], result['layers'][0]['config']):
            self.assertEqual(values['cli_auth_credentials_store'], 'file')
            self.assertEqual(values['api_key'], '[redacted:credential]')

    async def test_read_state_is_taken_from_response_level_and_list_receipts(self):
        session = await self.prepare()
        self.client.peer.request.return_value = {'thread': {'id': 'thread-1'}, 'readState': {'revision': 'r1', 'firstUnread': None}}
        await self.host.native.call('thread/read', {'threadId': 'thread-1', 'includeTurns': False}, None)
        self.assertEqual(session['read_state']['revision'], 'r1')
        self.client.peer.request.return_value = {'data': [{'id': 'thread-1'}], 'readStates': {'thread-1': {'revision': 'r2', 'firstUnread': {'type': 'threadStart'}}}}
        await self.host.native.call('thread/list', {}, None)
        self.assertEqual(session['read_state']['revision'], 'r2')
        self.assertEqual(self.store.read('sessions/source.json')['read_state']['revision'], 'r2')

    async def test_gateway_handoff_is_ephemeral_usable_and_redacted_from_events(self):
        await self.prepare()
        url = 'https://gateway.example/authorize?client_id=web&state=one-time'
        params = {'providerId': 'gateway', 'status': 'started', 'authUrl': url, 'error': None}
        await self.host.handle_notification({'method': 'account/gatewayOAuth/changed', 'params': params})
        app = ConsoleApp(self.host, self.events)
        response = await call(app, 'GET', '/api/native/auth-handoffs')
        self.assertEqual(payload(response)[0]['authUrl'], url)
        self.assertIn((b'cache-control', b'no-store'), response[0]['headers'])
        self.assertNotIn(url, str(redact(params, method='account/gatewayOAuth/changed')))
        with patch('codex_agent.native.time.time', return_value=10**12):
            self.assertEqual(self.host.native.list_auth_handoffs(), [])
        await self.host.handle_notification({'method': 'account/gatewayOAuth/changed', 'params': params})
        await self.host.native.call('account/gatewayOAuth/cancel', None, 'cancel-gateway')
        self.assertEqual(self.host.native.list_auth_handoffs(), [])
        await self.host.handle_notification({'method': 'account/gatewayOAuth/changed', 'params': params})
        await self.host.handle_notification({'method': 'account/gatewayOAuth/changed', 'params': {**params, 'status': 'succeeded', 'authUrl': None}})
        self.assertEqual(self.host.native.list_auth_handoffs(), [])

    async def test_receipt_read_recovers_without_replaying_a_process_or_storing_environment(self):
        await self.prepare()
        app = ConsoleApp(self.host, self.events)
        self.store.write('native-requests/process-recovery.json', {'digest': 'private-digest', 'result': {}})
        response = await call(app, 'GET', '/api/native/receipts/process-recovery')
        self.assertEqual(response[0]['status'], 200)
        self.assertEqual(payload(response)['result'], {})
        self.client.peer.request.assert_not_called()
        self.assertEqual((await call(app, 'GET', '/api/native/receipts/missing'))[0]['status'], 404)
