import copy
import unittest
from galatea_app.service import Context
from test_domain import DomainTests, spec


class GovernedFixture:
    def __init__(self):
        self.allowed = True

    def authorized(self, proposal):
        return self.allowed

    def owns(self, intent, operation):
        return operation.get("intent_key") == intent["idempotency_key"]


class TrainingTests(DomainTests):
    def setUp(self):
        super().setUp()
        self.service.command(
            self.user,
            self.task,
            "request_control",
            {"action": "cancel"},
            command_id="cancel-initial",
            expected_version=1,
        )
        self.adapter = GovernedFixture()
        self.service.governance = {"project_governance": self.adapter}
        self.task = self.service.create(
            self.user,
            command_id="training-task",
            goal="one governed smoke",
            constraints={"allow_training": True, "scope": "governed_training"},
            start=True,
        )["task_id"]
        s = spec("train", kind="training.run")
        s["execution"]["capability_ref"] = "governed-training/v1"
        s["acceptance"].append(
            {"id": "terminal", "type": "external_operation_terminal", "parameters": {}}
        )
        self.plan([s])
        self.proposal = {
            "immutable_inputs": {
                "route": "project_governance",
                "project_id": "fixture",
                "release_digest": "release",
                "config_digest": "config",
                "data_digest": "data",
                "split_digest": "split",
                "role": "smoke",
                "profile": "test",
                "final_test": False,
            },
            "objective": "one epoch",
            "resources": {
                "gpu": 1,
                "cpu": 2,
                "memory": {"worker_bytes": 1024, "object_store_bytes": None},
            },
            "hard_limits": {
                "attempts": 1,
                "retries": 0,
                "wall_seconds": 600,
                "memory_bytes": 1024,
            },
            "estimate": {"cost": None, "duration": None, "source": "not available"},
            "readiness": [],
            "expires_at": "2099-01-01T00:00:00Z",
        }

    def propose(self):
        return self.command(
            "propose_training",
            {"stage_id": "train", "proposal": self.proposal},
            expected_plan_revision=1,
        )["result"]

    def test_current_turn_task_requires_exact_training_confirmation_before_continuation(self):
        from dataclasses import replace
        self.command("request_control", {"action": "cancel"}, ctx=self.user,
                     expected_version=self.snapshot()["task"]["version"])
        agent = replace(self.agent, call_id="chat-start")
        self.task = self.service.create(agent, command_id="chat-training", goal="requested training",
            constraints={"allow_training": True}, in_current_turn=True)["task_id"]
        stage = spec("train", kind="training.run")
        stage["execution"]["capability_ref"] = "governed-training/v1"
        self.plan([stage])
        self.assertCode("AUTHORIZATION_REQUIRED", lambda: self.begin("train"))
        proposed = self.propose()
        self.assertCode("INVALID_TRANSITION", lambda: self.begin("train"))
        confirmed = replace(self.user, call_id="exact-training-decision")
        self.command("submit_input", {"request_id": proposed["request"]["request_id"],
            "values": {"proposal_digest": proposed["proposal"]["digest"], "decision": "confirm"}},
            ctx=confirmed, expected_version=self.snapshot()["task"]["version"])
        with self.service.storage.lock:
            aggregate = self.service._load(self.service.storage.db, agent, self.task)
            self.assertFalse(aggregate["in_current_turn"])
            self.assertEqual(aggregate["launch_receipt"], "exact-training-decision")
            self.assertEqual(self.service.storage.db.execute(
                "SELECT COUNT(*) FROM outbox WHERE task_id=? AND kind='continuation'", (self.task,)
            ).fetchone()[0], 1)
        self.assertTrue(self.begin("train")["attempt_id"])

    def decide(self, r, decision="confirm", key=None):
        return self.command(
            "submit_input",
            {
                "request_id": r["request"]["request_id"],
                "values": {
                    "proposal_digest": r["proposal"]["digest"],
                    "decision": decision,
                },
            },
            ctx=self.user,
            key=key,
            expected_version=self.snapshot()["task"]["version"],
        )

    def prepare(self):
        r = self.propose()
        self.decide(r)
        a = self.begin("train")
        p = self.command(
            "prepare_external",
            {
                **{k: a[k] for k in ("attempt_id", "execution_token")},
                "route": "project_governance",
                "immutable_inputs": self.proposal["immutable_inputs"],
            },
        )["result"]
        return a, p

    def bind(self, p):
        return self.command(
            "bind_external",
            {
                "intent_id": p["intent_id"],
                "operation_ref": {
                    "id": "operation",
                    "job_id": "job",
                    "intent_key": p["idempotency_key"],
                },
            },
        )

    def test_budget_confirm_does_not_create_governance_authorization(self):
        self.adapter.allowed = False
        r = self.propose()
        self.assertEqual(self.snapshot()["task"]["status"], "waiting_approval")
        self.assertCode("AUTHORIZATION_REQUIRED", lambda: self.decide(r))
        self.assertIsNone(self.snapshot()["stages"][0]["current_attempt"])

    def test_defer_reopen_read_only_and_explicit_reconfirm(self):
        r = self.propose()
        self.decide(r, "defer")
        self.assertEqual(self.snapshot()["task"]["status"], "paused")
        self.assertIsNone(self.snapshot()["stages"][0]["current_attempt"])
        self.service.get_context(self.agent, self.task)
        self.assertEqual(self.snapshot()["task"]["status"], "paused")
        self.decide(r)
        self.assertEqual(self.snapshot()["stages"][0]["status"], "ready")

    def test_new_proposal_invalidates_old_confirmation(self):
        old = self.propose()
        self.proposal["hard_limits"]["wall_seconds"] = 300
        self.propose()
        self.assertCode("VERSION_CONFLICT", lambda: self.decide(old))

    def test_F06_cancel_racing_submission_tracks_and_stops_late_operation(self):
        a, p = self.prepare()
        self.command(
            "request_control",
            {"action": "cancel"},
            ctx=self.user,
            expected_version=self.snapshot()["task"]["version"],
        )
        self.bind(p)
        self.assertEqual(self.snapshot()["task"]["status"], "cancelling")
        self.service.observe(
            self.task,
            "operation",
            {"status": "succeeded"},
            source="project_governance",
            message_id="late",
        )
        self.assertEqual(self.snapshot()["task"]["status"], "cancelled")
        self.assertEqual(
            self.snapshot()["external_operations"][0]["status"], "succeeded"
        )

    def test_confirmed_training_budget_blocks_extra_attempt(self):
        _, intent = self.prepare()
        self.bind(intent)
        self.service.observe(
            self.task,
            "operation",
            {"status": "failed"},
            source="project_governance",
            message_id="failed",
        )
        self.command("retry_stage", {"stage_id": "train"})
        self.assertCode("AUTHORIZATION_REQUIRED", lambda: self.begin("train"))

    def test_failed_training_replacement_requires_new_proposal_and_confirmation(self):
        _, intent = self.prepare()
        self.bind(intent)
        original = self.service.get_context(self.agent, self.task)["stage_specs"][0]
        replacement = copy.deepcopy(original)
        replacement.update(id="train_retry", replaces_stage_id="train")
        self.assertCode("PLAN_CONFLICT", lambda: self.plan(
            [original, replacement], revision=1))
        self.service.observe(
            self.task, "operation", {"status": "failed"},
            source="project_governance", message_id="failed-for-replacement",
        )
        failed = copy.deepcopy(self.snapshot()["stages"][0])
        self.plan([original, replacement], revision=1)
        self.assertCode("AUTHORIZATION_REQUIRED", lambda: self.begin("train_retry"))
        proposed = self.command(
            "propose_training", {"stage_id": "train_retry", "proposal": self.proposal},
            expected_plan_revision=2,
        )["result"]
        self.assertCode("INVALID_TRANSITION", lambda: self.begin("train_retry"))
        self.decide(proposed)
        attempt = self.begin("train_retry")
        self.assertNotEqual(attempt["attempt_id"], failed["current_attempt"]["id"])
        self.assertEqual(self.snapshot()["stages"][0], failed)
        self.assertEqual(self.snapshot()["stages"][1]["current_attempt"]["number"], 1)

    def test_foreign_operation_artifact_is_rejected(self):
        attempt, intent = self.prepare()
        self.bind(intent)
        self.assertCode(
            "FORBIDDEN",
            lambda: self.command(
                "report_result",
                {
                    **attempt,
                    "result": {},
                    "artifact_candidates": [
                        {
                            "role": "report",
                            "slot": "report",
                            "media_type": "application/json",
                            "external_ref": {
                                "route": "project_governance",
                                "operation_id": "another-operation",
                                "run_id": "foreign",
                                "path": "reports/result.json",
                            },
                        }
                    ],
                },
            ),
        )

    def test_B12_duplicate_terminal_only_one_continuation(self):
        _, p = self.prepare()
        self.bind(p)
        self.service.observe(
            self.task,
            "operation",
            {"status": "succeeded"},
            source="project_governance",
            message_id="first",
        )
        self.service.observe(
            self.task,
            "operation",
            {"status": "succeeded"},
            source="project_governance",
            message_id="second",
        )
        with self.service.storage.lock:
            count = self.service.storage.db.execute(
                "SELECT count(*) FROM outbox WHERE task_id=? AND dedupe_key LIKE '%:external:%'",
                (self.task,),
            ).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertNotEqual(self.snapshot()["stages"][0]["status"], "completed")

    def test_F08_unknown_does_not_allow_retry(self):
        _, p = self.prepare()
        self.bind(p)
        self.service.observe(
            self.task,
            "operation",
            {"status": "unknown"},
            source="project_governance",
            message_id="missing",
        )
        self.assertEqual(self.snapshot()["task"]["status"], "blocked")
        self.assertCode("INVALID_TRANSITION", lambda: self.begin("train"))


# Reuse setup/helpers without collecting the base acceptance cases a second time.
for name in list(vars(DomainTests)):
    if name.startswith("test_") and name not in vars(TrainingTests):
        setattr(TrainingTests, name, None)
del DomainTests
