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
    def begin_external(self, b):
        stage = test_domain.spec("train", kind="training.run")
        stage["execution"].update(
            mode="external", capability_ref="governed-training/v1"
        )
        b.plan([stage], revision=1)
        proposal = b.command(
            "propose_training", {"stage_id": "train", "proposal": b.proposal},
            expected_plan_revision=2,
        )["result"]
        b.decide(proposal)
        return b.begin("train")

    def runtime(self, b, turn="turn", **overrides):
        # Simulate already delivered start/decision turns and the scoped Host API.
        with b.service.storage.transaction() as db:
            a = b.service._load(db, b.agent, b.task)
            a["host_continuation_binding"] = "binding"
            b.service._save(db, a, increment=False)
            db.execute("UPDATE outbox SET state='delivered' WHERE task_id=? AND kind='continuation'", (b.task,))
        return {
            "binding_id": "binding", "state": "active", "session_id": "session",
            "thread_id": "thread", "session_status": "ready", "active_turn_id": None,
            "pending_continuation": False, "last_turn_id": turn, "last_turn_status": "completed",
            **overrides,
        }

    def recovery_count(self, b):
        return b.service.storage.db.execute(
            "SELECT COUNT(*) FROM outbox WHERE task_id=? AND kind='continuation' "
            "AND dedupe_key LIKE '%:idle_turn:%'", (b.task,),
        ).fetchone()[0]

    async def test_recovery_covers_before_begin_and_after_intent_without_new_attempt(self):
        for point in ("ready", "begun", "prepared"):
            b = test_training.TrainingTests()
            b.setUp()
            try:
                intent_ids = []
                if point == "ready":
                    b.decide(b.propose())
                else:
                    attempt = self.begin_external(b)
                    if point == "prepared":
                        result = b.command("prepare_external", {
                            **{k: attempt[k] for k in ("attempt_id", "execution_token")},
                            "route": "project_governance",
                            "immutable_inputs": b.proposal["immutable_inputs"],
                        })["result"]
                        intent_ids = [result["intent_id"]]
                before = b.snapshot()["stages"]
                runtime = self.runtime(b)
                if intent_ids:
                    b.service.reconcile_idle_task(b.task, runtime)
                    self.assertEqual(self.recovery_count(b), 0)
                b.service.reconcile_idle_task(b.task, runtime, reconciled_intents=intent_ids)
                b.service.reconcile_idle_task(b.task, runtime, reconciled_intents=intent_ids)
                self.assertEqual(self.recovery_count(b), 1, point)
                self.assertEqual(b.snapshot()["stages"], before)
                self.assertEqual(b.snapshot()["external_operations"], [])
            finally:
                b.tearDown()

    async def test_two_unproductive_recoveries_block_and_progress_resets_counter(self):
        b = test_domain.DomainTests()
        b.setUp()
        try:
            b.plan()
            b.service.reconcile_idle_task(b.task, self.runtime(b, "one"))
            b.service.reconcile_idle_task(b.task, self.runtime(b, "two"))
            # Actual stage progress resets the counter, unlike reads and timestamps.
            b.begin()
            b.service.reconcile_idle_task(b.task, self.runtime(b, "three"))
            b.service.reconcile_idle_task(b.task, self.runtime(b, "four"))
            b.service.reconcile_idle_task(b.task, self.runtime(b, "five"))
            self.assertEqual(self.recovery_count(b), 4)
            self.assertEqual(b.snapshot()["task"]["status"], "blocked")
            self.assertIn("连续两次", b.snapshot()["task"]["blocked_reason"])
        finally:
            b.tearDown()

    async def test_recovery_requires_idle_matching_active_binding(self):
        for overrides in (
            {"session_status": "running"}, {"active_turn_id": "new-turn"},
            {"pending_continuation": True}, {"state": "revoked"}, {"state": "expired"},
            {"thread_id": "other"}, {"session_id": "other"}, {"last_turn_id": None},
            {"binding_id": "other"},
        ):
            b = test_domain.DomainTests()
            b.setUp()
            try:
                b.plan()
                b.service.reconcile_idle_task(b.task, self.runtime(b, **overrides))
                self.assertEqual(self.recovery_count(b), 0, overrides)
            finally:
                b.tearDown()

    async def test_recovery_honors_controls_budget_and_failed_turn(self):
        for condition in ("current_turn", "paused", "budget", "deadline", "interrupted", "unauthorized"):
            b = test_training.TrainingTests()
            b.setUp()
            try:
                self.begin_external(b)
                runtime = self.runtime(b)
                with b.service.storage.transaction() as db:
                    a = b.service._load(db, b.agent, b.task)
                    if condition == "current_turn":
                        a["in_current_turn"] = True
                    elif condition == "paused":
                        a["control_requested"] = "pause"
                    elif condition == "budget":
                        a["continuations"] = a["snapshot"]["task"]["constraints"]["max_auto_continuations"]
                    elif condition == "deadline":
                        a["snapshot"]["task"]["constraints"]["deadline_at"] = "2000-01-01T00:00:00Z"
                    elif condition == "unauthorized":
                        a["execution_authorized"] = False
                    else:
                        runtime["last_turn_status"] = "interrupted"
                    b.service._save(db, a)
                b.service.reconcile_idle_task(b.task, runtime)
                self.assertEqual(self.recovery_count(b), 0, condition)
                if condition in ("budget", "deadline", "interrupted"):
                    self.assertEqual(b.snapshot()["task"]["status"], "blocked")
                    self.assertTrue(b.snapshot()["task"]["blocked_reason"])
            finally:
                b.tearDown()

    async def test_worker_recovers_once_across_restart_and_reports_host_errors(self):
        b = test_training.TrainingTests()
        b.setUp()
        try:
            self.begin_external(b)
            runtime = self.runtime(b)
            host = Host()
            original = host.request
            async def request(method, path, **kwargs):
                if path == "/internal/v1/runtime-events":
                    return {"events": [], "next_cursor": "cursor"}
                if method == "GET" and "/continuation-bindings/" in path:
                    return runtime
                return await original(method, path, **kwargs)
            host.request = request
            await Worker(b.service, host).tick()
            await Worker(b.service, host).tick()
            self.assertEqual(self.recovery_count(b), 1)
            self.assertEqual(sum(path == "/internal/v1/continuations" for path, _ in host.calls), 1)
            async def fail(*args, **kwargs):
                raise TimeoutError()
            host.request = fail
            worker = Worker(b.service, host)
            await worker.tick()
            self.assertEqual(worker.last_error, "host_reconciliation:TimeoutError")
        finally:
            b.tearDown()

    async def test_poll_timestamps_do_not_count_as_progress_but_status_reversal_does(self):
        b = test_training.TrainingTests()
        b.setUp()
        try:
            _, intent = b.prepare()
            b.bind(intent)
            facts = iter([
                {"status": "running", "observed_at": "one"},
                {"status": "running", "observed_at": "two"},
                {"status": "unknown", "observed_at": "three"},
                {"status": "running", "observed_at": "four"},
            ])
            b.adapter.observe = lambda operation: next(facts)
            worker = Worker(b.service, Host())
            await worker.tick()
            version = b.snapshot()["task"]["version"]
            seq = b.snapshot()["last_event_seq"]
            await worker.tick()
            self.assertEqual(b.snapshot()["task"]["version"], version)
            self.assertEqual(b.snapshot()["last_event_seq"], seq)
            await worker.tick()
            self.assertEqual(b.snapshot()["external_operations"][0]["status"], "unknown")
            await worker.tick()
            self.assertEqual(b.snapshot()["external_operations"][0]["status"], "running")
        finally:
            b.tearDown()

    async def test_unknown_or_unreconciled_submission_never_starts_another_turn(self):
        for condition in ("unknown", "reconcile_failed", "waiting", "pending_delivery"):
            b = test_training.TrainingTests()
            b.setUp()
            try:
                _, intent = b.prepare()
                runtime = self.runtime(b)
                reconciled = []
                with b.service.storage.transaction() as db:
                    a = b.service._load(db, b.agent, b.task)
                    if condition == "unknown":
                        a["intents"][intent["intent_id"]]["state"] = "unknown"
                        reconciled = [intent["intent_id"]]
                    elif condition == "pending_delivery":
                        b.service._enqueue_continuation(db, a, "fixture", "pending")
                    b.service._save(db, a, increment=False)
                if condition == "waiting":
                    b.bind(intent)
                b.service.reconcile_idle_task(b.task, runtime, reconciled_intents=reconciled)
                self.assertEqual(self.recovery_count(b), 0, condition)
            finally:
                b.tearDown()

    async def test_terminal_failure_gets_bounded_diagnosis_then_visible_block(self):
        b = test_training.TrainingTests()
        b.setUp()
        try:
            _, intent = b.prepare()
            b.bind(intent)
            b.service.observe(b.task, "operation", {"status": "failed"}, source="project_governance", message_id="failed")
            for turn in ("diagnose", "retry-diagnose", "no-progress"):
                b.service.reconcile_idle_task(b.task, self.runtime(b, turn))
            self.assertEqual(self.recovery_count(b), 2)
            self.assertEqual(b.snapshot()["task"]["status"], "blocked")
            self.assertEqual(len(b.snapshot()["external_operations"]), 1)
        finally:
            b.tearDown()

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

    async def test_terminal_job_missing_evidence_requests_one_diagnostic_turn(self):
        b = test_training.TrainingTests()
        b.setUp()
        try:
            _, intent = b.prepare()
            b.bind(intent)
            fact = {
                "status": "unknown", "ray_status": "SUCCEEDED",
                "reason": "Unique bound MLflow Run not found",
            }
            for message_id in ("first", "next"):
                b.service.observe(
                    b.task, "operation", fact,
                    source="project_governance", message_id=message_id,
                )
            self.assertEqual(b.snapshot()["task"]["status"], "blocked")
            self.assertEqual(b.snapshot()["external_operations"][0]["status"], "unknown")
            self.assertEqual(b.service.storage.db.execute(
                "SELECT COUNT(*) FROM outbox WHERE task_id=? "
                "AND dedupe_key LIKE '%:external_evidence_inconsistent:%'",
                (b.task,),
            ).fetchone()[0], 1)
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
