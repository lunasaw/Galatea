from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codex_agent.catalog import Catalog
from codex_agent.events import EventLog
from codex_agent.host import AgentHost, HostNotReady
from codex_agent.registry import ToolContext, ToolRegistry
from codex_agent.state import AtomicJsonStore, OperationJournal


class FakeClient:
    def __init__(self):
        self.thread_counter = 0
        self.turn_counter = 0
        self.interrupts = []
        self.resumes = []
        self.turns = []

    async def start_thread(self, **kwargs):
        self.thread_counter += 1
        return {"thread": {"id": f"thread-{self.thread_counter}"}}

    async def start_turn(self, thread_id, text):
        self.turn_counter += 1
        self.turns.append((thread_id, text))
        return {"turn": {"id": f"turn-{self.turn_counter}"}}

    async def interrupt(self, thread_id, turn_id):
        self.interrupts.append((thread_id, turn_id))
        return {"ok": True}

    async def read_thread(self, thread_id):
        return {"thread": {"id": thread_id, "turns": []}}

    async def resume(self, thread_id):
        self.resumes.append(thread_id)
        return {"thread": {"id": thread_id}}


class HostTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.store = AtomicJsonStore(root / "state")
        self.events = EventLog(root / "events")
        self.catalog = Catalog.load(ROOT / "config/contracts")
        self.calls = []

        def handler(context, arguments):
            self.calls.append((context, arguments))
            return {"observed": True}

        registry = ToolRegistry(self.catalog, OperationJournal(self.store),
                                {name: handler for name in self.catalog.schemas})
        self.client = FakeClient()
        self.host = AgentHost(registry=registry, event_log=self.events, store=self.store,
                              client_factory=lambda: self.client, reconciler=lambda: None,
                              principal_id="p", project_ids={"project"}, campaign_ids={"campaign"},
                              actions={"galatea_get_capabilities"})
        await self.host.start()

    async def asyncTearDown(self):
        await self.host.stop()
        self.directory.cleanup()

    async def test_session_message_is_idempotent_and_calls_native_client_once(self):
        session = await self.host.create_session("session-1")
        first = await self.host.send_message(session["id"], "request-1", "hello")
        second = await self.host.send_message(session["id"], "request-1", "hello")
        self.assertEqual(first, second)
        self.assertEqual(len(self.client.turns), 1)
        self.assertEqual(self.store.read("requests/request-1.json")["turn_id"], first["turn_id"])

    async def test_conflicting_request_id_is_rejected(self):
        await self.host.create_session("session-1")
        await self.host.send_message("session-1", "request-1", "hello")
        with self.assertRaises(ValueError):
            await self.host.send_message("session-1", "request-1", "different")

    async def test_dynamic_tool_request_is_bound_to_session_and_returns_envelope(self):
        session = await self.host.create_session("session-1")
        message = await self.host.send_message(session["id"], "request-1", "hello")
        response = await self.host.handle_tool_call({"id": 7, "params": {
            "threadId": session["thread_id"], "turnId": message["turn_id"],
            "callId": "call-1", "namespace": "galatea", "tool": "galatea_get_capabilities",
            "arguments": {}}})
        self.assertTrue(response["result"]["success"])
        envelope = json.loads(response["result"]["contentItems"][0]["text"])
        self.assertTrue(envelope["ok"])
        self.assertEqual(self.calls[0][1], {})

    async def test_unknown_call_enters_fail_closed_response_without_handler(self):
        session = await self.host.create_session("session-1")
        message = await self.host.send_message(session["id"], "request-1", "hello")
        response = await self.host.handle_tool_call({"id": 8, "params": {
            "threadId": session["thread_id"], "turnId": message["turn_id"],
            "callId": "call-unknown", "namespace": "galatea", "tool": "galatea_submit_job",
            "arguments": {}}})
        envelope = json.loads(response["result"]["contentItems"][0]["text"])
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["category"], "forbidden")
        self.assertEqual(self.calls, [])

    async def test_replayed_call_with_different_arguments_returns_conflict(self):
        session = await self.host.create_session("session-1")
        message = await self.host.send_message(session["id"], "request-1", "hello")
        first = {"id": 10, "params": {"threadId": session["thread_id"], "turnId": message["turn_id"],
                "callId": "same-call", "namespace": "galatea", "tool": "galatea_get_capabilities", "arguments": {}}}
        await self.host.handle_tool_call(first)
        second = {"id": 11, "params": {**first["params"], "arguments": {"protocol_version": "galatea.tools/v1"}}}
        response = await self.host.handle_tool_call(second)
        self.assertEqual(json.loads(response["result"]["contentItems"][0]["text"])["error"]["category"], "conflict")

    async def test_interrupt_stays_pending_until_completion_and_resume_reuses_thread(self):
        session = await self.host.create_session("session-1")
        message = await self.host.send_message(session["id"], "request-1", "hello")
        pending = await self.host.interrupt("session-1")
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(self.client.interrupts, [(session["thread_id"], message["turn_id"])])
        with self.assertRaises(HostNotReady):
            await self.host.resume("session-1")
        await self.host.handle_notification({"method": "turn/completed", "params": {
            "threadId": session["thread_id"], "turn": {"id": message["turn_id"], "status": "interrupted"}}})
        resumed = await self.host.resume("session-1")
        self.assertEqual(resumed["thread_id"], session["thread_id"])
        self.assertEqual(self.client.resumes, [session["thread_id"]])

    async def test_readiness_fails_when_reconciler_stale(self):
        self.host._reconciler_last_success = 0
        with self.assertRaises(HostNotReady):
            self.host.assert_ready(max_age=0.1)

    async def test_persisted_session_denies_tool_calls_until_verified_resume(self):
        session = await self.host.create_session("session-1")
        message = await self.host.send_message(session["id"], "request-1", "hello")
        await self.host.stop()
        replacement = AgentHost(registry=self.host.registry, event_log=self.events, store=self.store,
                                client_factory=lambda: self.client, reconciler=lambda: None,
                                principal_id="p", project_ids={"project"}, campaign_ids={"campaign"},
                                actions={"galatea_get_capabilities"})
        await replacement.start()
        response = await replacement.handle_tool_call({"id": 9, "params": {
            "threadId": session["thread_id"], "turnId": message["turn_id"], "callId": "restart-call",
            "namespace": "galatea", "tool": "galatea_get_capabilities", "arguments": {}}})
        self.assertFalse(response["result"]["success"])
        await replacement.stop()

    async def test_resume_blocks_catalog_digest_drift(self):
        session = await self.host.create_session("session-1")
        session["catalog_digest"] = "changed"
        self.store.write("sessions/session-1.json", session)
        with self.assertRaises(HostNotReady):
            await self.host.resume("session-1")


if __name__ == "__main__":
    unittest.main()
