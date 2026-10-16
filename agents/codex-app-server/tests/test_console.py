from pathlib import Path
import asyncio
import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from codex_agent.console.server import ConsoleApp
from codex_agent.events import EventLog
from codex_agent.state import AtomicJsonStore


class FakeHost:
    def __init__(self, events, root):
        self.events = events
        self.principal_id = 'p'
        self.store = AtomicJsonStore(root / 'state')
        self.registry = SimpleNamespace(catalog=SimpleNamespace(digest='catalog'), dynamic_tools=lambda _: [])
        self.actions = set()
        self.messages = []
        self.sessions = {'s': {'id': 's', 'principal_id': 'p', 'status': 'ready'}}

    def assert_ready(self): pass
    async def start(self): pass
    async def stop(self): pass
    def list_sessions(self): return list(self.sessions.values())

    async def create_session(self, session_id=None):
        session_id = session_id or 'new'
        self.sessions[session_id] = {'id': session_id, 'principal_id': 'p', 'status': 'ready'}
        return self.sessions[session_id]

    async def send_message(self, session_id, request_id, text):
        self.messages.append((session_id, request_id, text))
        return {'request_id': request_id, 'turn_id': 'turn-1', 'status': 'accepted'}

    def get_session(self, session_id): return self.sessions.get(session_id)


async def call(app, method, path, body=None, headers=None):
    sent = []
    url = urlsplit(path)
    scope = {'type': 'http', 'method': method, 'scheme': 'http', 'path': url.path,
             'query_string': url.query.encode(), 'server': ('localhost', 80),
             'headers': [(key.encode(), value.encode()) for key, value in
                         {'host': 'localhost', **(headers or {})}.items()]}
    messages = [{'type': 'http.request', 'body': json.dumps(body).encode() if body is not None else b'', 'more_body': False}]
    async def receive(): return messages.pop(0) if messages else {'type': 'http.disconnect'}
    async def send(message): sent.append(message)
    await app(scope, receive, send)
    return sent


def payload(sent):
    return json.loads(b''.join(m.get('body', b'') for m in sent))


class ConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        events = EventLog(epoch='epoch')
        await events.start()
        events.append('s', {'kind': 'message_accepted', 'host_request_id': 'r0', 'public': {'text': 'hello'}})
        await events.flush()
        self.host = FakeHost(events, Path(self.tmp.name))
        self.app = ConsoleApp(self.host, events)

    async def asyncTearDown(self):
        await self.host.events.close()
        self.tmp.cleanup()

    async def test_anonymous_read_write_and_persistent_idempotency(self):
        for route in ('/', '/api/sessions', '/api/sessions/s', '/api/sessions/s/snapshot', '/api/events/history', '/api/console/status'):
            self.assertEqual((await call(self.app, 'GET', route))[0]['status'], 200, route)
        for _ in range(2):
            sent = await call(self.app, 'POST', '/api/sessions/s/messages', {'request_id': 'r', 'text': 'x'})
            self.assertEqual(sent[0]['status'], 200)
        self.assertEqual(self.host.messages, [('s', 'r', 'x')])
        replacement = ConsoleApp(self.host, self.host.events)
        await call(replacement, 'POST', '/api/sessions/s/messages', {'request_id': 'r', 'text': 'x'})
        self.assertEqual(len(self.host.messages), 1)
        self.assertEqual((await call(self.app, 'POST', '/api/sessions/s/messages', {'request_id': 'r', 'text': 'changed'}))[0]['status'], 409)

    async def test_same_origin_protection_without_identity_authentication(self):
        for headers in ({'origin': 'https://evil.example'}, {'sec-fetch-site': 'cross-site'}, {'origin': 'null'}):
            self.assertEqual((await call(self.app, 'POST', '/api/sessions', {}, headers={'idempotency-key': 'r', **headers}))[0]['status'], 403)
        sent = await call(self.app, 'POST', '/api/sessions', {}, headers={'origin': 'http://localhost', 'idempotency-key': 'r'})
        self.assertEqual(sent[0]['status'], 200)

    async def test_cursor_errors_and_explicit_last_event_id_conflict(self):
        for cursor, code, status in [('1','INVALID_CURSOR',400), ('old:0','EPOCH_MISMATCH',409), ('epoch:999','CURSOR_AHEAD',409)]:
            sent = await call(self.app, 'GET', '/api/events/history?after=' + cursor)
            self.assertEqual(sent[0]['status'], status)
            self.assertEqual(payload(sent)['error'], code)
        sent = await call(self.app, 'GET', '/api/sessions/s/events?after=epoch:0', headers={'last-event-id': 'epoch:1'})
        self.assertEqual(sent[0]['status'], 400)
        self.assertEqual((await call(self.app, 'GET', '/api/events/epoch:1'))[0]['status'], 200)
        self.assertEqual(payload(await call(self.app, 'GET', '/api/events/epoch:2'))['error'], 'UNKNOWN_EVENT')
        self.assertEqual(payload(await call(self.app, 'GET', '/api/sessions/missing/history'))['error'], 'UNKNOWN_SESSION')

    async def test_capabilities_and_snapshot_retain_long_text_and_scope(self):
        caps = payload(await call(self.app, 'GET', '/api/capabilities'))
        self.assertEqual(caps['console_role'], 'training_agent_chat_trace')
        self.assertTrue(caps['skill_integration'])
        self.assertTrue(caps['interactive_requests'])
        snapshot = payload(await call(self.app, 'GET', '/api/sessions/s/snapshot'))
        self.assertEqual(snapshot['messages'][0]['text'], 'hello')
        self.assertEqual(snapshot['watermark'], 'epoch:1')
        self.assertEqual(payload(await call(self.app, 'GET', '/api/sessions/s/history?method=absent'))['next_cursor'], 'epoch:1')

    async def test_sse_replays_epoch_cursor_and_redacts_credentials(self):
        self.host.events.append('s', {'kind': 'tool_call', 'public': {'tool': 'x', 'token': 'secret'}})
        await self.host.events.flush()
        sent = await call(self.app, 'GET', '/api/sessions/s/events?after=epoch:1')
        body = b''.join(m.get('body', b'') for m in sent)
        self.assertIn(b'id: epoch:2', body)
        self.assertIn(b'event: observation', body)
        self.assertNotIn(b'secret', body)
