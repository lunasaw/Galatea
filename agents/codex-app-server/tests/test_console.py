from pathlib import Path
import asyncio
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from codex_agent.console.server import ConsoleApp
from codex_agent.events import EventLog
from codex_agent.state import AtomicJsonStore


class FakeHost:
    def __init__(self, events):
        self.events = events
        self.principal_id = "p"
        self.store = AtomicJsonStore(events.root / "state")
        self.messages = []
        self.sessions = {"s": {"id": "s", "principal_id": "p", "status": "ready"}}

    def assert_ready(self):
        return None

    async def create_session(self, session_id=None):
        session_id = session_id or "new"
        self.sessions[session_id] = {"id": session_id, "principal_id": "p", "status": "ready"}
        return self.sessions[session_id]

    async def send_message(self, session_id, request_id, text):
        self.messages.append((session_id, request_id, text))
        return {"request_id": request_id, "turn_id": "turn-1", "status": "accepted"}

    def get_session(self, session_id):
        return self.sessions.get(session_id)


async def call(app, method, path, body=None, headers=None):
    sent = []
    body_bytes = json.dumps(body).encode() if body is not None else b""
    scope = {"type": "http", "method": method, "path": path, "query_string": b"after=1",
             "headers": [(b"authorization", b"Bearer test-token-1234567890"), (b"origin", b"http://localhost")]
             + [(key.encode(), value.encode()) for key, value in (headers or {}).items()]}
    messages = [{"type": "http.request", "body": body_bytes, "more_body": False}]
    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}
    async def send(message):
        sent.append(message)
    await app(scope, receive, send)
    return sent


class ConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        events = EventLog(Path(self.tmp.name))
        events.append("s", {"kind": "message", "public": {"text": "hello"}})
        self.host = FakeHost(events)
        self.app = ConsoleApp(self.host, events, token="test-token-1234567890", allowed_origins={"http://localhost"})

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_health_is_anonymous_but_write_requires_bearer(self):
        sent = []
        async def receive(): return {"type": "http.disconnect"}
        async def send(message): sent.append(message)
        await self.app({"type": "http", "method": "GET", "path": "/health/live", "query_string": b"", "headers": []}, receive, send)
        self.assertEqual(sent[0]["status"], 200)
        sent = await call(self.app, "POST", "/api/sessions/s/messages", {"request_id": "r", "text": "x"},
                          headers={"authorization": "Bearer wrong"})
        self.assertEqual(sent[0]["status"], 401)

    async def test_write_rejects_cross_origin_and_message_is_idempotent(self):
        sent = await call(self.app, "POST", "/api/sessions/s/messages", {"request_id": "r", "text": "x"},
                          headers={"origin": "https://evil.example"})
        self.assertEqual(sent[0]["status"], 403)
        sent = await call(self.app, "POST", "/api/sessions/s/messages", {"request_id": "r", "text": "x"})
        self.assertEqual(sent[0]["status"], 200)
        self.assertEqual(self.host.messages, [("s", "r", "x")])

    async def test_sse_replays_after_sequence_and_does_not_leak_secret(self):
        self.host.events.append("s", {"kind": "tool_call", "public": {"tool": "x", "token": "secret"}})
        sent = await call(self.app, "GET", "/api/sessions/s/events")
        payload = b"".join(message.get("body", b"") for message in sent)
        self.assertIn(b'"seq":2', payload)
        self.assertNotIn(b"secret", payload)


if __name__ == "__main__":
    unittest.main()
