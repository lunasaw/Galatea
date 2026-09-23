import asyncio
import json
from pathlib import Path
import tempfile
import unittest
import test_console as console
from codex_agent.events import EventLog


class EventDurabilityTests(unittest.TestCase):
    def test_reserved_fields_and_private_fields_cannot_override_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            log = EventLog(Path(directory))
            event = log.append('s', {'seq': 99, 'session_id': 'other', 'kind': 'native', 'prompt': 'secret',
                                      'public': {'status': 'ready', 'reasoning': 'private'}})
            self.assertEqual(event['seq'], 1)
            self.assertEqual(event['session_id'], 's')
            self.assertNotIn('secret', json.dumps(event))
            self.assertNotIn('private', json.dumps(event))

    def test_truncated_tail_produces_durable_observation_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            log = EventLog(Path(directory))
            log.append('s', {'kind': 'session'})
            with (Path(directory) / 's.jsonl').open('ab') as stream:
                stream.write(b'{"seq":2')
            recovered = EventLog(Path(directory))
            recovered.append('s', {'kind': 'session'})
            events = recovered.replay('s')
            self.assertEqual([e['seq'] for e in events], [1, 2, 3])
            self.assertEqual(events[1]['kind'], 'observation_gap')
            self.assertEqual(events[1]['public']['from_seq'], 2)


class ConsoleStreamTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = console.ConsoleTests.asyncSetUp
    asyncTearDown = console.ConsoleTests.asyncTearDown

    async def test_single_large_body_is_rejected(self):
        sent = await console.call(self.app, 'POST', '/api/sessions/s/messages', {'request_id': 'r', 'text': 'x' * (1024*1024)})
        self.assertEqual(sent[0]['status'], 413)
        self.assertFalse(self.host.messages)

    async def test_live_stream_delivers_appended_events_and_disconnects(self):
        incoming, sent = asyncio.Queue(), asyncio.Queue()
        incoming.put_nowait({'type': 'http.request', 'body': b''})
        scope = {'type': 'http', 'method': 'GET', 'path': '/api/sessions/s/events', 'query_string': b'',
                 'headers': [(b'authorization', b'Bearer test-token-1234567890'), (b'last-event-id', b'1')]}
        task = asyncio.create_task(self.app(scope, incoming.get, sent.put))
        self.addAsyncCleanup(self.cancel, task)
        self.assertEqual((await asyncio.wait_for(sent.get(), .3))['status'], 200)
        self.host.events.append('s', {'kind': 'turn', 'public': {'status': 'completed'}})
        body = await asyncio.wait_for(sent.get(), .6)
        self.assertIn(b'"seq":2', body['body'])
        self.assertTrue(body['more_body'])
        incoming.put_nowait({'type': 'http.disconnect'})
        await asyncio.wait_for(task, .6)

    @staticmethod
    async def cancel(task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
