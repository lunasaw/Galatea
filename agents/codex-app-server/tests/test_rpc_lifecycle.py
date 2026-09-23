import asyncio
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from codex_agent.rpc import JsonRpcPeer, ProtocolError


class Writer:
    def __init__(self):
        self.messages = asyncio.Queue()
    def write(self, data):
        self.messages.put_nowait(json.loads(data))
    async def drain(self):
        pass


class RPCLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reader, self.writer = asyncio.StreamReader(), Writer()
        self.notifications, self.calls = asyncio.Queue(), asyncio.Queue()
        async def notify(message):
            await self.notifications.put(message)
        async def call(message):
            await self.calls.put(message)
            return {'result': {'success': True}}
        self.peer = JsonRpcPeer(self.reader, self.writer, call, notification=notify, timeout=.1)

    async def asyncTearDown(self):
        await self.peer.close()

    def feed(self, message):
        self.reader.feed_data(json.dumps(message).encode() + b'\n')

    async def test_receives_native_messages_after_request_completes(self):
        pending = asyncio.create_task(self.peer.request('turn/start'))
        request = await self.writer.messages.get()
        self.feed({'id': request['id'], 'result': {'turn': {'id': 't'}}})
        await pending
        self.feed({'method': 'item/tool/call', 'id': 'native', 'params': {}})
        self.feed({'method': 'turn/completed', 'params': {'turn': {'id': 't'}}})
        self.assertEqual((await asyncio.wait_for(self.calls.get(), .2))['id'], 'native')
        self.assertEqual((await asyncio.wait_for(self.notifications.get(), .2))['method'], 'turn/completed')
        self.assertTrue((await self.writer.messages.get())['result']['success'])

    async def test_concurrent_responses_are_matched_out_of_order(self):
        first = asyncio.create_task(self.peer.request('first'))
        second = asyncio.create_task(self.peer.request('second'))
        requests = [await self.writer.messages.get(), await self.writer.messages.get()]
        for request in reversed(requests):
            self.feed({'id': request['id'], 'result': request['method']})
        self.assertEqual(await asyncio.gather(first, second), ['first', 'second'])

    async def test_timeout_and_eof_fail_all_requests(self):
        with self.assertRaises(TimeoutError):
            await self.peer.request('never')
        with self.assertRaises((ProtocolError, EOFError, TimeoutError)):
            await self.peer.request('after-timeout')
        self.assertFalse(self.peer.healthy)

    async def test_duplicate_server_request_cannot_execute_twice(self):
        self.peer.start()
        message = {'id': 'native', 'method': 'item/tool/call', 'params': {}}
        self.feed(message)
        await asyncio.wait_for(self.calls.get(), .2)
        self.feed(message)
        await asyncio.wait_for(self.peer.wait_closed(), .2)
        self.assertFalse(self.peer.healthy)
        self.assertTrue(self.calls.empty())
