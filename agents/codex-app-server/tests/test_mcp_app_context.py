import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_host_core
from test_console import call, payload
from codex_agent.console.server import ConsoleApp
from codex_agent.mcp_app_context import app_identity, message_input, update_context, with_app_context
from codex_agent.state import AtomicJsonStore


class McpAppContextTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = test_host_core.HostTests.asyncTearDown
    async def asyncSetUp(self):
        await test_host_core.HostTests.asyncSetUp(self)
        self.session = await self.host.create_session('app-session')
        self.scope = {'threadId': self.session['thread_id'], 'server': 'fixture',
                      'uri': 'ui://fixture/app', 'instanceId': 'call-1'}
        self.app = ConsoleApp(self.host, self.events)
        self.client.start_turn = AsyncMock(return_value={'turn': {'id': 'turn-app'}})
        self.client.peer = SimpleNamespace(healthy=True, request=AsyncMock(return_value={'turnId': 'turn-app'}))

    async def post(self, route, body, key):
        return await call(self.app, 'POST', '/api/mcp-app/' + route, {**self.scope, **body}, {'idempotency-key': key})

    async def test_context_replaces_persists_and_does_not_trigger_a_turn(self):
        for i in range(2):
            sent = await self.post('context', {'context': {'structuredContent': {'runId': str(i)}}}, 'context-' + str(i))
            self.assertEqual(sent[0]['status'], 200)
        self.client.start_turn.assert_not_awaited()
        restored = AtomicJsonStore(self.store.root)
        options = with_app_context(restored, 'app-session')
        entry = options['additionalContext'][app_identity(self.scope)]
        self.assertEqual(entry['kind'], 'untrusted')
        self.assertEqual(json.loads(entry['value'])['structuredContent']['runId'], '1')
        await self.host.send_message('app-session', 'next-message', 'Analyze')
        self.assertEqual(self.client.start_turn.call_args.kwargs['options'], options)
        # Retry identity is based on the user's request, not changing app state.
        update_context(self.store, 'app-session', {**self.scope, 'context': {'structuredContent': {'runId': 'new'}}})
        await self.host.send_message('app-session', 'next-message', 'Analyze')
        self.client.start_turn.assert_awaited_once()

    async def test_context_isolated_by_session_server_and_instance_and_clear(self):
        for instance, server in [('call-1', 'fixture'), ('call-2', 'fixture'), ('call-1', 'other')]:
            update_context(self.store, 'app-session', {**self.scope, 'instanceId': instance, 'server': server,
                           'context': {'content': [{'type': 'text', 'text': instance}]}})
        self.assertEqual(len(with_app_context(self.store, 'app-session')['additionalContext']), 3)
        self.assertIsNone(with_app_context(self.store, 'another-session'))
        update_context(self.store, 'app-session', {**self.scope, 'context': {}})
        entries = with_app_context(self.store, 'app-session')['additionalContext']
        self.assertNotIn(app_identity(self.scope), entries)
        self.assertEqual(len(entries), 2)

    async def test_message_starts_then_steers_original_thread_and_deduplicates(self):
        await self.post('context', {'context': {'structuredContent': {'runId': '42'}}}, 'context')
        body = {'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'Continue'}]}}
        for _ in range(2):
            result = await self.post('messages', body, 'message')
            self.assertEqual(result[0]['status'], 200, payload(result))
        self.client.start_turn.assert_awaited_once()
        self.assertEqual(self.client.start_turn.call_args.args[0], self.scope['threadId'])
        await self.post('messages', body, 'steer')
        method, params = self.client.peer.request.call_args.args
        self.assertEqual(method, 'turn/steer')
        self.assertEqual(params['expectedTurnId'], 'turn-app')
        self.assertEqual(params['threadId'], self.scope['threadId'])
        self.assertIn(app_identity(self.scope), params['additionalContext'])

    async def test_unknown_message_outcome_is_not_replayed(self):
        self.host.native.call = AsyncMock(side_effect=TimeoutError())
        body = {'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'Continue'}]}}
        await self.post('messages', body, 'uncertain')
        result = await self.post('messages', body, 'uncertain')
        self.assertEqual(result[0]['status'], 503)
        self.host.native.call.assert_awaited_once()

    async def test_scope_origin_size_and_modality_validation(self):
        for body in [{'threadId': 'unknown'}, {'uri': 'https://example.com'}]:
            result = await self.post('context', {**body, 'context': {}}, 'bad-' + next(iter(body)))
            self.assertNotEqual(result[0]['status'], 200)
        for i, context in enumerate([{'content': [{'type': 'image'}]}, {'structuredContent': []},
                                     {'content': [{'type': 'text', 'text': 'x' * (256 * 1024)}]}]):
            result = await self.post('context', {'context': context}, 'invalid-' + str(i))
            self.assertEqual(result[0]['status'], 409)
        result = await call(self.app, 'POST', '/api/mcp-app/context', {**self.scope, 'context': {}},
                            {'origin': 'null', 'idempotency-key': 'cross-origin'})
        self.assertEqual(result[0]['status'], 403)
        for message in [{'role': 'assistant', 'content': []}, {'role': 'user', 'content': [{'type': 'resource_link', 'uri': 'file:///secret'}]},
                        {'role': 'user', 'content': [{'type': 'image', 'mimeType': 'image/png', 'data': 'bad!'}]}]:
            with self.assertRaises(ValueError):
                message_input(message)
        inputs = message_input({'role': 'user', 'content': [{'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(b'image').decode()}]})
        self.assertEqual(inputs[0]['url'], 'data:image/png;base64,aW1hZ2U=')
