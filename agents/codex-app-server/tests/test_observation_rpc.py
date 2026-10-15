import asyncio
import json
import unittest
import test_rpc_lifecycle as rpc_tests
from codex_agent.console.service import ConsoleObservationService
from codex_agent.console.models import EventQuery
from codex_agent.rpc import JsonRpcPeer, AppServerClient


class RPCObservationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = ConsoleObservationService(epoch='epoch')
        await self.service.start()
        self.reader, self.writer = asyncio.StreamReader(), rpc_tests.Writer()
        self.peer = JsonRpcPeer(self.reader, self.writer, observer=self.service.observer,
                                diagnostic=self.service.diagnostic, runtime_instance_id='runtime', timeout=.1)
        self.addAsyncCleanup(self.service.close)
        self.addAsyncCleanup(self.peer.close)

    def feed(self, message):
        self.reader.feed_data(json.dumps(message).encode() + b'\n')

    async def rows(self):
        await self.service.flush()
        return (await self.service.read_events(EventQuery()))['events']

    async def test_initialization_unknown_requests_and_errors_all_observed(self):
        client = AppServerClient(self.peer)
        task = asyncio.create_task(client.initialize())
        sent = await self.writer.messages.get()
        self.feed({'id': sent['id'], 'result': {}})
        await task
        await self.writer.messages.get()
        self.feed({'id': 0, 'method': 'future/request', 'params': {}})
        reply = await asyncio.wait_for(self.writer.messages.get(), .2)
        self.assertEqual(reply['error']['code'], -32601)
        self.feed({'method': 'future/notification', 'params': {}})
        await asyncio.sleep(.01)
        rows = [row for row in await self.rows() if row['source'] == 'codex']
        self.assertEqual([row['kind'] for row in rows], ['request', 'response', 'notification', 'request', 'response', 'notification'])
        self.assertEqual(rows[0]['method'], 'initialize')
        self.assertEqual(rows[4]['method'], 'future/request')

    async def test_write_before_drain_failure_and_duplicate_response_are_visible(self):
        async def broken_drain():
            raise ConnectionError('broken pipe')
        self.writer.drain = broken_drain
        with self.assertRaises(ConnectionError):
            await self.peer.send({'method': 'outgoing'})
        rows = await self.rows()
        self.assertEqual(rows[0]['method'], 'outgoing')
        self.assertEqual(rows[1]['kind'], 'send_failed')
        self.assertEqual(rows[1]['payload']['phase'], 'drain')
        self.assertEqual(rows[1]['payload']['outbound_event_id'], rows[0]['event_id'])
        self.writer.drain = rpc_tests.Writer().drain
        task = asyncio.create_task(self.peer.request('request'))
        await self.writer.messages.get()
        sent = await self.writer.messages.get()
        response = {'id': sent['id'], 'result': {}}
        self.feed(response)
        await task
        self.feed(response)
        await self.peer.wait_closed()
        rows = await self.rows()
        self.assertEqual(sum(row['kind'] == 'response' for row in rows), 2)
        self.assertTrue(any(row['kind'] == 'protocol_error' for row in rows))

    async def test_observer_exception_does_not_change_rpc_result(self):
        def broken(*args, **kwargs):
            raise RuntimeError('observer failed')
        self.peer.observer = broken
        task = asyncio.create_task(self.peer.request('ok'))
        sent = await self.writer.messages.get()
        self.feed({'id': sent['id'], 'result': {'ok': True}})
        self.assertEqual(await task, {'ok': True})
        self.assertTrue(any(row['kind'] == 'observation_error' for row in await self.rows()))
