import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from codex_agent.state import AtomicJsonStore
from codex_agent.background_apps import BackgroundApps


class BackgroundTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AtomicJsonStore(Path(self.tmp.name))
        self.calls = []
        self.session = {
            "id": "session",
            "thread_id": "thread",
            "status": "ready",
            "principal_id": "alice",
        }

        async def send(session, id, text, **kwargs):
            self.calls.append((session, id, text, kwargs))
            r = {"status": "accepted", "turn_id": "turn"}
            self.store.write(f"requests/{id}.json", r)
            return r

        async def create(id, **kwargs):
            return {**self.session, "id": id, "cwd": kwargs["options"]["cwd"]}

        self.host = SimpleNamespace(
            store=self.store,
            principal_id="alice",
            get_session=lambda id: self.session,
            send_message=send,
            create_session=create,
        )
        self.worker = BackgroundApps(self.host, workspaces={"work": self.tmp.name})
        self.store.write(
            "app-calls/call.json",
            {
                "state": "completed",
                "principal_id": "alice",
                "session_id": "session",
                "write": True,
                "identity": {"arguments": {"start": True}},
                "expires_at": 9999999999,
            },
        )
        self.binding = self.worker.bind(
            {
                "request_id": "bind",
                "host_session_id": "session",
                "launch_receipt": "call",
                "source_ref": "opaque-task",
                "expires_at": "2099-01-01T00:00:00Z",
                "max_continuations": 2,
            },
            "integration",
        )

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def enqueue(self, id="request"):
        return self.worker.accept(
            {
                "request_id": id,
                "host_session_id": "session",
                "binding_id": self.binding["binding_id"],
                "not_before": "2000-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "context": {
                    "kind": "untrusted",
                    "source": "integration",
                    "summary": "external job finished",
                },
            },
            "integration",
        )

    async def test_busy_queues_and_replay_starts_once(self):
        self.enqueue()
        self.session["status"] = "running"
        await self.worker.tick()
        self.assertEqual(self.calls, [])
        self.session["status"] = "ready"
        await self.worker.tick()
        self.enqueue()
        await self.worker.tick()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            self.calls[0][3]["options"]["additionalContext"]["background"]["kind"], "untrusted"
        )

    async def test_unknown_native_result_is_not_resent(self):
        async def fail(*args, **kwargs):
            self.calls.append(args)
            raise TimeoutError()

        self.host.send_message = fail
        self.enqueue()
        await self.worker.tick()
        await self.worker.tick()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.worker.get("request", "integration")["state"], "unknown")

    async def test_revoke_prevents_pending_dispatch(self):
        self.enqueue()
        self.worker.revoke(self.binding["binding_id"], "integration")
        await self.worker.tick()
        self.assertEqual(self.calls, [])

    async def test_binding_status_is_scoped_and_reconciles_completed_receipts(self):
        id = self.binding["binding_id"]
        self.enqueue()
        self.assertTrue(self.worker.binding_status(id, "integration")["pending_continuation"])
        await self.worker.tick()
        self.assertTrue(self.worker.binding_status(id, "integration")["pending_continuation"])
        self.store.write("requests/request.json", {"status": "completed", "turn_id": "turn"})
        self.session.update(last_turn_id="turn", last_turn_status="completed")
        # The read is correct even before the inbox worker's next tick.
        status = self.worker.binding_status(id, "integration")
        self.assertFalse(status["pending_continuation"])
        self.assertEqual(status["last_turn_id"], "turn")
        self.assertNotIn("launch_receipt", status)
        self.worker.revoke(id, "integration")
        self.assertEqual(self.worker.binding_status(id, "integration")["state"], "revoked")
        with self.assertRaises(ValueError):
            self.worker.binding_status(id, "another-service")

    async def test_unknown_dispatch_prevents_idle_recovery(self):
        self.enqueue()
        row = self.worker.get("request", "integration")
        row["state"] = "unknown"
        self.store.write("continuations/request.json", row)
        self.assertTrue(self.worker.binding_status(self.binding["binding_id"], "integration")["pending_continuation"])

    async def test_crash_before_binding_counter_write_cannot_expand_budget(self):
        self.enqueue("one")
        self.enqueue("two")
        path = f"continuation-bindings/{self.binding['binding_id']}.json"
        row = self.store.read(path)
        row["count"] = 1
        self.store.write(path, row)
        with self.assertRaises(ValueError):
            self.enqueue("three")

    async def test_wrong_service_and_wrong_session_rejected(self):
        with self.assertRaises(ValueError):
            self.worker.get("request", "other")
        with self.assertRaises(ValueError):
            self.worker.bind(
                {
                    "request_id": "bad",
                    "host_session_id": "other",
                    "launch_receipt": "call",
                    "source_ref": "x",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "max_continuations": 2,
                },
                "integration",
            )

    async def test_activation_requires_scope_and_reuses_session(self):
        self.store.write(
            "app-calls/launch.json",
            {
                "state": "completed",
                "principal_id": "alice",
                "session_id": "session",
                "write": True,
                "identity": {"arguments": {"activation_id": "activation", "workspace_ref": "work"}},
            },
        )
        request = {
            "activation_id": "activation",
            "launch_receipt": "launch",
            "workspace_ref": "work",
            "create_session": True,
        }
        first = await self.worker.activate(request, "integration")
        second = await self.worker.activate(request, "integration")
        self.assertEqual(first, second)
        self.assertEqual(first["state"], "ready")
