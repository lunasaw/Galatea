from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codex_agent.catalog import Catalog
from codex_agent.events import EventLog
from codex_agent.host import AgentHost, HostNotReady, ResumeRejected
from codex_agent.registry import ToolContext, ToolRegistry
from codex_agent.state import AtomicJsonStore, OperationJournal
from codex_agent.console.models import SnapshotQuery


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
        self.events = EventLog()
        self.catalog = Catalog.load(ROOT / "tests/fixtures/dynamic-tools")
        self.calls = []

        def handler(context, arguments):
            self.calls.append((context, arguments))
            return {"observed": True}

        registry = ToolRegistry(self.catalog, OperationJournal(self.store),
                                {name: handler for name in self.catalog.schemas})
        self.client = FakeClient()
        self.host = AgentHost(registry=registry, event_log=self.events, store=self.store,
                              client_factory=lambda: self.client, reconciler=lambda: None,
                              principal_id="p",
                              actions={"galatea_get_capabilities"})
        await self.host.start()

    async def asyncTearDown(self):
        await self.host.stop()
        await self.events.close()
        self.directory.cleanup()

    async def test_session_message_is_idempotent_and_calls_native_client_once(self):
        session = await self.host.create_session("session-1")
        first = await self.host.send_message(session["id"], "request-1", "hello")
        second = await self.host.send_message(session["id"], "request-1", "hello")
        self.assertEqual(first, second)
        self.assertEqual(len(self.client.turns), 1)
        self.assertEqual(self.store.read("requests/request-1.json")["turn_id"], first["turn_id"])
        await self.events.flush()
        snapshot = await self.events.snapshot(SnapshotQuery('session-1'))
        self.assertEqual([m['text'] for m in snapshot['messages']], ['hello'])

    async def test_created_session_exposes_effective_native_defaults(self):
        self.client.start_thread = AsyncMock(return_value={
            'thread': {'id': 'inherited'}, 'model': 'native-model', 'reasoningEffort': 'high',
            'approvalPolicy': 'never', 'sandbox': {'type': 'readOnly', 'networkAccess': False}})
        session = await self.host.create_session('defaults')
        self.assertEqual(session['settings']['approvalPolicy'], 'never')
        self.assertEqual(session['settings']['sandboxPolicy']['type'], 'readOnly')
        self.assertEqual(session['settings']['model'], 'native-model')
        self.assertEqual(session['settings']['effort'], 'high')
        self.assertNotIn('approvalPolicy', self.client.start_thread.call_args.kwargs)

    async def test_native_settings_notification_updates_visible_settings_without_private_instructions(self):
        session = await self.host.create_session('reload')
        await self.host.handle_notification({'method': 'thread/settings/updated', 'params': {
            'threadId': session['thread_id'], 'threadSettings': {
                'approvalPolicy': 'never', 'sandboxPolicy': {'type': 'readOnly'}, 'summary': 'none',
                'collaborationMode': {'mode': 'plan', 'settings': {'model': 'native', 'reasoning_effort': 'high',
                    'developer_instructions': 'private built-in instructions'}},
                'activePermissionProfile': None}}})
        current = self.host.get_session('reload')['settings']
        self.assertEqual(current['approvalPolicy'], 'never')
        self.assertEqual(current['sandboxPolicy']['type'], 'readOnly')
        self.assertEqual(current['summary'], 'none')
        self.assertIsNone(current['collaborationMode']['settings']['developer_instructions'])

    async def test_conflicting_request_id_is_rejected(self):
        await self.host.create_session("session-1")
        await self.host.send_message("session-1", "request-1", "hello")
        with self.assertRaises(ValueError):
            await self.host.send_message("session-1", "request-1", "different")

    async def test_durable_intent_failure_blocks_native_turn_and_accepted_message(self):
        await self.host.create_session('session-1')
        with patch.object(self.store, 'write', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                await self.host.send_message('session-1', 'request-1', 'hello')
        self.assertEqual(self.client.turns, [])
        self.assertIsNone(self.store.read('requests/request-1.json'))
        await self.events.flush()
        self.assertEqual((await self.events.snapshot(SnapshotQuery('session-1')))['messages'], [])

    async def test_observation_commit_failure_does_not_change_durable_execution(self):
        await self.host.create_session('session-1')
        await self.events.flush()
        with patch.object(self.events.store, 'commit', side_effect=OSError('synthetic observer failure')):
            record = await self.host.send_message('session-1', 'request-1', 'hello')
            await asyncio.sleep(.01)
            self.assertEqual((await self.events.status())['observation']['status'], 'unavailable')
            self.host.assert_ready()
            self.assertEqual(record['status'], 'accepted')
            self.assertEqual(self.store.read('requests/request-1.json')['status'], 'accepted')
            self.assertEqual(len(self.client.turns), 1)
        await self.events.retry()
        await self.events.flush()

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
                                principal_id="p",
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

    async def test_resume_reports_extension_drift_without_loading_native_thread(self):
        self.host.bindings['extensions_sha256'] = 'old'
        await self.host.create_session('s')
        self.host.bindings['extensions_sha256'] = 'new'
        self.client.read_thread = AsyncMock()
        with self.assertRaises(ResumeRejected) as rejected:
            await self.host.resume('s')
        self.assertEqual(rejected.exception.code, 'session-extensions-changed')
        self.client.read_thread.assert_not_called()
        self.assertEqual(self.client.resumes, [])
        self.assertEqual(self.store.read('sessions/s.json')['extensions_sha256'], 'old')

    async def test_resume_reports_unresolved_operation_without_resuming(self):
        await self.host.create_session('s')
        self.store.write('tool-calls/c.json', {'identity': {'session_id': 's'}, 'state': 'executing'})
        with self.assertRaises(ResumeRejected) as rejected:
            await self.host.resume('s')
        self.assertEqual(rejected.exception.code, 'session-operation-unresolved')
        self.assertEqual(self.client.resumes, [])

    async def test_native_resume_failure_is_not_a_safe_preflight_rejection(self):
        await self.host.create_session('s')
        self.client.resume = AsyncMock(side_effect=HostNotReady('connection lost after resume'))
        with self.assertRaises(HostNotReady) as failed:
            await self.host.resume('s')
        self.assertNotIsInstance(failed.exception, ResumeRejected)

    async def test_source_resume_rebinds_only_extension_drift_after_native_idle_check(self):
        self.host.bindings['extensions_sha256'] = 'old'
        original = await self.host.create_session('s')
        self.host.bindings['extensions_sha256'] = 'new'
        def rebind(session, bindings):
            updated = {**session, 'extensions_sha256': bindings['extensions_sha256']}
            self.store.write('sessions/s.json', updated)
            return updated
        self.host.extension_rebinder = AsyncMock(side_effect=rebind)
        result = await self.host.resume('s')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['extensions_sha256'], 'new')
        self.assertEqual(result['thread_id'], original['thread_id'])
        self.assertEqual(self.client.resumes, [original['thread_id']])
        self.host.extension_rebinder.assert_awaited_once()

    async def test_extension_migration_does_not_run_for_active_native_turn_or_other_drift(self):
        self.host.bindings['extensions_sha256'] = 'old'
        original = await self.host.create_session('s')
        self.host.bindings['extensions_sha256'] = 'new'
        self.host.extension_rebinder = AsyncMock()
        self.client.read_thread = AsyncMock(return_value={'thread': {
            'id': original['thread_id'], 'turns': [{'id': 'running', 'status': 'inProgress'}]}})
        with self.assertRaises(ResumeRejected) as active:
            await self.host.resume('s')
        self.assertEqual(active.exception.code, 'session-turn-active')
        self.host.bindings['agent_config_sha256'] = 'changed'
        with self.assertRaises(ResumeRejected) as mismatch:
            await self.host.resume('s')
        self.assertEqual(mismatch.exception.code, 'session-binding-mismatch')
        self.host.extension_rebinder.assert_not_awaited()
        self.assertEqual(self.client.resumes, [])

    async def test_failed_extension_preflight_does_not_resume_native_thread(self):
        self.host.bindings['extensions_sha256'] = 'old'
        await self.host.create_session('s')
        self.host.bindings['extensions_sha256'] = 'new'
        self.host.extension_rebinder = AsyncMock(side_effect=ValueError('files changed again'))
        with self.assertRaises(ResumeRejected) as rejected:
            await self.host.resume('s')
        self.assertEqual(rejected.exception.code, 'session-extensions-changed')
        self.assertEqual(self.client.resumes, [])
        self.assertEqual(self.store.read('sessions/s.json')['extensions_sha256'], 'old')


if __name__ == "__main__":
    unittest.main()
