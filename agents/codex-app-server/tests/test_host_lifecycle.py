import asyncio
import json
import unittest
import test_host_core as core
from codex_agent.host import HostNotReady


class HostLifecycleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = core.HostTests.asyncSetUp
    asyncTearDown = core.HostTests.asyncTearDown

    async def complete(self, session, turn):
        await self.host.handle_notification({'method': 'turn/completed', 'params': {
            'threadId': session['thread_id'], 'turn': {'id': turn['turn_id'], 'status': 'completed'}}})

    async def test_concurrent_retry_creates_one_turn_and_stores_no_prompt(self):
        session = await self.host.create_session('s')
        results = await asyncio.gather(*(self.host.send_message('s', 'r', 'private prompt') for _ in range(8)))
        self.assertEqual(len(self.client.turns), 1)
        self.assertTrue(all(result == results[0] for result in results))
        self.assertNotIn('private prompt', json.dumps(self.store.read('requests/r.json')))

    async def test_capacity_and_session_busy_last_until_completion(self):
        self.host.max_active_turns = 1
        first = await self.host.create_session('s')
        second = await self.host.create_session('s2')
        turn = await self.host.send_message('s', 'r', 'one')
        for session_id in ('s', 's2'):
            with self.assertRaises(HostNotReady):
                await self.host.send_message(session_id, 'r2', 'two')
        await self.complete(first, turn)
        await self.host.send_message(second['id'], 'r2', 'two')

    async def test_turn_intent_exists_before_native_rpc_and_unknown_is_not_retried(self):
        await self.host.create_session('s')
        calls = []
        async def crash(*args):
            calls.append(args)
            self.assertEqual(self.store.read('requests/r.json')['status'], 'intent_recorded')
            raise EOFError('lost response after submission')
        self.client.start_turn = crash
        with self.assertRaises(EOFError):
            await self.host.send_message('s', 'r', 'one')
        result = await self.host.send_message('s', 'r', 'one')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(len(calls), 1)

    async def test_close_waits_for_completion_and_closed_session_rejects_messages(self):
        session = await self.host.create_session('s')
        turn = await self.host.send_message('s', 'r', 'one')
        result = await self.host.close_session('s')
        self.assertEqual(result['status'], 'closing')
        await self.complete(session, turn)
        self.assertEqual(self.host.get_session('s')['status'], 'closed')
        with self.assertRaises(HostNotReady):
            await self.host.send_message('s', 'r2', 'two')

    async def test_wrong_turn_tool_call_and_private_notifications_are_not_exposed(self):
        session = await self.host.create_session('s')
        await self.host.send_message('s', 'r', 'one')
        result = await self.host.handle_tool_call({'id': 1, 'params': {'threadId': session['thread_id'],
            'turnId': 'wrong', 'callId': 'call', 'namespace': 'galatea', 'tool': 'galatea_get_capabilities', 'arguments': {}}})
        self.assertFalse(result['result']['success'])
        await self.host.handle_notification({'method': 'item/reasoning/textDelta', 'params': {
            'threadId': session['thread_id'], 'delta': 'private reasoning'}})
        self.assertNotIn('private reasoning', json.dumps(self.events.replay('s')))

    async def test_handler_timeout_keeps_loop_responsive_and_blocks_resume(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            release.wait(2)
            return {'ok': True}
        self.host.registry.handlers['galatea_get_capabilities'] = slow
        self.host.tool_timeout = .03
        session = await self.host.create_session('s')
        turn = await self.host.send_message('s', 'r', 'one')
        try:
            response = await self.host.handle_tool_call({'id': 12, 'params': {
                'threadId': session['thread_id'], 'turnId': turn['turn_id'], 'callId': 'slow',
                'namespace': 'galatea', 'tool': 'galatea_get_capabilities', 'arguments': {}}})
            self.assertTrue(entered.is_set())
            error = json.loads(response['result']['contentItems'][0]['text'])['error']
            self.assertEqual(error['state_changed'], 'unknown')
            self.assertEqual(self.host.get_session('s')['status'], 'recovery_required')
            with self.assertRaises(HostNotReady):
                await self.host.resume('s')
        finally:
            release.set()

    async def test_completed_reconciler_task_fails_readiness(self):
        self.host._reconcile_task.cancel()
        await asyncio.gather(self.host._reconcile_task, return_exceptions=True)
        with self.assertRaises(HostNotReady):
            self.host.assert_ready()
