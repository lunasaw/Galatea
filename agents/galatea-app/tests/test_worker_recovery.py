import unittest
import test_domain
from galatea_app.workers import Worker
from galatea_app.common import TaskError
import test_training


class Host:
    def __init__(self):
        self.calls = []

    async def request(self, method, path, **kwargs):
        self.calls.append((path, kwargs))
        return {"binding_id": "binding", "state": "accepted"}


class WorkerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_lost_bind_response_recovers_original_operation_without_submission(
        self,
    ):
        b = test_training.TrainingTests()
        b.setUp()
        try:
            _, intent = b.prepare()
            b.adapter.reconcile = lambda durable: {
                "id": "original-job",
                "job_id": "original-job",
                "intent_key": durable["idempotency_key"],
            }
            b.adapter.observe = lambda operation: {"status": "running"}
            await Worker(b.service, Host()).tick()
            ops = b.snapshot()["external_operations"]
            self.assertEqual(len(ops), 1)
            self.assertEqual(ops[0]["id"], "original-job")
            await Worker(b.service, Host()).tick()
            self.assertEqual(len(b.snapshot()["external_operations"]), 1)
        finally:
            b.tearDown()

    async def test_cancel_local_attempt_finishes_only_after_host_interrupt_ack(self):
        b = test_domain.DomainTests()
        b.setUp()
        try:
            b.plan()
            b.begin()
            b.command(
                "request_control",
                {"action": "cancel"},
                ctx=b.user,
                expected_version=b.snapshot()["task"]["version"],
            )
            self.assertEqual(b.snapshot()["task"]["status"], "cancelling")
            await Worker(b.service, Host()).tick()
            self.assertEqual(b.snapshot()["task"]["status"], "cancelled")
        finally:
            b.tearDown()

    async def test_resume_uses_new_host_binding_and_receipt(self):
        b = test_domain.DomainTests()
        b.setUp()
        try:
            host = Host()
            worker = Worker(b.service, host)
            await worker.tick()
            b.command(
                "request_control",
                {"action": "pause"},
                ctx=b.user,
                expected_version=b.snapshot()["task"]["version"],
            )
            await worker.tick()
            b.command(
                "request_control",
                {"action": "resume"},
                ctx=b.user,
                expected_version=b.snapshot()["task"]["version"],
            )
            await worker.tick()
            calls = [
                kw["json"]
                for path, kw in host.calls
                if path == "/internal/v1/continuation-bindings"
            ]
            self.assertEqual(len(calls), 2)
            self.assertNotEqual(calls[0]["request_id"], calls[1]["request_id"])
            self.assertRegex(calls[1]["request_id"], r"^[A-Za-z0-9_.-]+$")
        finally:
            b.tearDown()

    async def test_restored_backup_does_not_verify_or_activate(self):
        from galatea_app.service import TaskService
        from unittest.mock import AsyncMock

        b = test_domain.DomainTests()
        b.setUp()
        try:
            b.plan()
            a = b.begin()
            (b.root / "report.json").write_text('{"rows":2}')
            b.command(
                "report_result",
                {
                    **a,
                    "result": {},
                    "artifact_candidates": [
                        {
                            "path": "report.json",
                            "role": "report",
                            "slot": "report",
                            "media_type": "application/json",
                        }
                    ],
                },
            )
            dest = b.root / "restore.sqlite3"
            b.service.storage.backup(dest)
            restored = TaskService(dest, workspaces={"work": b.root})
            try:
                projects = type("Projects", (), {"tick": AsyncMock()})()
                host = Host()
                worker = Worker(restored, host, projects=projects)
                await worker.tick()
                self.assertEqual(
                    restored.get_task(b.agent, b.task)["stages"][0]["status"],
                    "verifying",
                )
                projects.tick.assert_not_awaited()
                self.assertEqual(host.calls, [])
                with self.assertRaises(TaskError) as caught:
                    restored.create(
                        b.user,
                        command_id="restore-create",
                        goal="must not start",
                        start=True,
                    )
                self.assertEqual(caught.exception.code, "UNAVAILABLE")
            finally:
                restored.close()
        finally:
            b.tearDown()

    async def test_wall_budget_requests_governed_stop_but_keeps_observing(self):
        import json, time

        b = test_training.TrainingTests()
        b.setUp()
        try:
            _, intent = b.prepare()
            b.bind(intent)
            with b.service.storage.transaction() as db:
                a = b.service._load(db, b.agent, b.task)
                a["external_deadlines"] = {"operation": 0}
                b.service._save(db, a)
            calls = []
            b.adapter.observe = lambda op: {"status": "running"}
            b.adapter.stop = lambda op: calls.append(op["id"])
            await Worker(b.service, Host()).tick()
            self.assertEqual(calls, ["operation"])
            self.assertEqual(
                b.snapshot()["external_operations"][0]["status"], "running"
            )
            self.assertEqual(b.snapshot()["task"]["status"], "cancelling")
        finally:
            b.tearDown()

    async def test_completion_revokes_already_queued_native_continuations(self):
        b = test_domain.DomainTests()
        b.setUp()
        try:
            host = Host()
            worker = Worker(b.service, host)
            await worker.tick()
            b.plan()
            b.finish()
            b.finish("clean", filename="clean.json")
            b.command(
                "complete_task",
                {
                    "summary": "done",
                    "artifact_refs": [x["id"] for x in b.snapshot()["artifacts"]],
                },
                expected_plan_revision=1,
            )
            await worker.tick()
            self.assertTrue(
                any(
                    path == "/internal/v1/continuation-bindings/binding"
                    for path, kw in host.calls
                )
            )
            self.assertEqual(b.snapshot()["task"]["status"], "completed")
        finally:
            b.tearDown()
