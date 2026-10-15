import asyncio
import json
import unittest
import test_console as console


class ConsoleStreamTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = console.ConsoleTests.asyncSetUp
    asyncTearDown = console.ConsoleTests.asyncTearDown

    async def test_single_large_body_is_rejected(self):
        sent = await console.call(self.app, 'POST', '/api/sessions/s/messages', {'request_id': 'r', 'text': 'x' * (1024*1024)})
        self.assertEqual(sent[0]['status'], 413)
        self.assertFalse(self.host.messages)

    async def stream(self, query=b'after=epoch:1'):
        incoming, sent = asyncio.Queue(), asyncio.Queue()
        incoming.put_nowait({'type': 'http.request', 'body': b''})
        scope = {'type': 'http', 'method': 'GET', 'path': '/api/sessions/s/events', 'query_string': query, 'headers': []}
        task = asyncio.create_task(self.app(scope, incoming.get, sent.put))
        self.addAsyncCleanup(self.cancel, task)
        self.assertEqual((await asyncio.wait_for(sent.get(), .3))['status'], 200)
        return incoming, sent, task

    async def test_live_stream_two_clients_disconnect_and_replay(self):
        one, two = await self.stream(), await self.stream()
        self.host.events.append('s', {'kind': 'turn', 'public': {'status': 'completed'}})
        for incoming, sent, task in (one, two):
            chunks = b''
            async with asyncio.timeout(1):
                while b'event: observation' not in chunks:
                    chunks += (await sent.get()).get('body', b'')
            self.assertIn(b'id: epoch:2', chunks)
            incoming.put_nowait({'type': 'http.disconnect'})
            await asyncio.wait_for(task, .5)
        self.assertEqual(self.app._streams, 0)
        self.assertEqual((await self.host.events.status())['observation']['event_count'], 2)

    async def test_binding_control_ignores_method_filter_and_precedes_events(self):
        incoming, sent, task = await self.stream(b'after=epoch:1&method=missing')
        self.host.events.bind('s', 'thread')
        chunks = b''
        async with asyncio.timeout(1):
            while b'snapshot_required' not in chunks:
                chunks += (await sent.get()).get('body', b'')
        self.assertIn(b'binding_changed', chunks)
        incoming.put_nowait({'type': 'http.disconnect'})
        await asyncio.wait_for(task, .5)

    async def test_blocked_browser_does_not_block_capture_or_another_subscriber(self):
        incoming = asyncio.Queue()
        incoming.put_nowait({'type': 'http.request', 'body': b''})
        blocked = asyncio.Event()
        async def slow_send(message):
            if message['type'] == 'http.response.body':
                blocked.set()
                await asyncio.Event().wait()
        scope = {'type': 'http', 'method': 'GET', 'path': '/api/sessions/s/events',
                 'query_string': b'after=epoch:1', 'headers': []}
        slow = asyncio.create_task(self.app(scope, incoming.get, slow_send))
        self.addAsyncCleanup(self.cancel, slow)
        await asyncio.wait_for(blocked.wait(), .5)
        healthy_incoming, healthy_sent, healthy = await self.stream()
        for index in range(200):
            self.host.events.append('s', {'kind': 'fixture', 'public': {'index': index}})
        await self.host.events.flush()
        self.assertEqual((await self.host.events.status())['observation']['event_count'], 201)
        chunks = b''
        async with asyncio.timeout(1):
            while b'id: epoch:201' not in chunks:
                chunks += (await healthy_sent.get()).get('body', b'')
        self.assertIn(b'event: observation', chunks)
        healthy_incoming.put_nowait({'type': 'http.disconnect'})
        await asyncio.wait_for(healthy, .5)
        await self.cancel(slow)
        self.assertEqual(self.app._streams, 0)

    @staticmethod
    async def cancel(task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
