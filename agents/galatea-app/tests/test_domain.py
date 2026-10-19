"""Business acceptance against real SQLite and actual bounded artifact reads."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

from galatea_app.service import Context, TaskService, TaskError


def spec(id="inspect", dependencies=None, required=True, kind="data.inspect"):
    return dict(
        id=id,
        title=id,
        kind=kind,
        depends_on=dependencies or [],
        required=required,
        input_refs=[],
        output_slots=[
            {"name": "report", "role": "report", "media_type": "application/json"}
        ],
        acceptance=[
            {
                "id": id + "_report",
                "type": "artifact_exists",
                "parameters": {"artifact_role": "report"},
            }
        ],
        execution={
            "mode": "skill",
            "capability_ref": "data-local/v1",
            "skill_refs": [],
            "retry_policy": {"max_attempts": 2, "requires_reconciliation": True},
        },
    )


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "input.csv").write_text("x\n1\n1\n")
        self.db = self.root / "tasks.sqlite3"
        self.service = TaskService(self.db, workspaces={"work": self.root})
        self.user = Context(
            "alice",
            "host",
            "session",
            "thread",
            "work",
            role="app",
            confirmed=True,
            interaction_id="launch-confirmation",
        )
        self.agent = Context(
            "alice", "host", "session", "thread", "work", turn_id="turn"
        )
        self.counter = 0
        self.task = self.service.create(
            self.user,
            command_id="create",
            goal="clean data",
            inputs=[{"path": "input.csv"}],
            constraints={"allow_training": False},
            start=True,
        )["task_id"]

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def command(self, name, payload=None, ctx=None, key=None, **preconditions):
        self.counter += 1
        return self.service.command(
            ctx or self.agent,
            self.task,
            name,
            payload or {},
            command_id=key or f"cmd-{self.counter}",
            **preconditions,
        )

    def plan(self, stages=None, revision=0):
        return self.command(
            "replace_plan",
            {
                "reason": "initial plan",
                "stages": stages or [spec(), spec("clean", ["inspect"])],
            },
            expected_plan_revision=revision,
        )

    def begin(self, stage="inspect", key=None):
        return self.command(
            "begin_stage",
            {"stage_id": stage},
            key=key,
            expected_plan_revision=self.snapshot()["task"]["plan_revision"],
        )["result"]

    def snapshot(self):
        return self.service.get_task(self.agent, self.task)

    def finish(self, stage="inspect", attempt=None, filename="report.json"):
        attempt = attempt or self.begin(stage)
        (self.root / filename).write_text('{"rows": 2}')
        r = self.command(
            "report_result",
            {
                "stage_id": stage,
                "attempt_id": attempt["attempt_id"],
                "execution_token": attempt["execution_token"],
                "result": {"rows": 2},
                "artifact_candidates": [
                    {
                        "path": filename,
                        "role": "report",
                        "slot": "report",
                        "media_type": "application/json",
                    }
                ],
            },
        )
        self.assertEqual(
            self.snapshot()["stages"][
                next(
                    i
                    for i, s in enumerate(self.snapshot()["stages"])
                    if s["id"] == stage
                )
            ]["status"],
            "verifying",
        )
        self.service.verify_pending()
        return r

    def assertCode(self, code, action):
        with self.assertRaises(TaskError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_B01_two_stage_delivery_and_snapshot_contract(self):
        self.plan()
        self.finish()
        self.finish("clean", filename="clean.json")
        self.command(
            "complete_task",
            {
                "summary": "clean data delivered",
                "artifact_refs": [a["id"] for a in self.snapshot()["artifacts"]],
            },
            expected_plan_revision=1,
        )
        snapshot = self.snapshot()
        self.assertEqual(snapshot["task"]["status"], "completed")
        self.assertEqual(len(snapshot["stages"]), 2)
        self.assertEqual(snapshot["external_operations"], [])
        schema = json.loads(
            (
                Path(__file__).parents[1] / "docs/contracts/task-snapshot.schema.json"
            ).read_text()
        )
        Draft202012Validator(schema).validate(snapshot)
        self.assertTrue(
            all(e["verification"] == "verified" for e in snapshot["evidence"])
        )

    def test_B02_insert_repair_preserves_completed_evidence(self):
        self.plan()
        self.finish()
        previous = self.snapshot()["stages"][0]
        self.plan(
            [spec(), spec("repair", ["inspect"]), spec("clean", ["repair"])], revision=1
        )
        self.assertEqual(self.snapshot()["stages"][0], previous)
        self.assertEqual(self.snapshot()["task"]["plan_revision"], 2)

    def test_B03_running_spec_is_frozen(self):
        self.plan()
        self.begin()
        changed = spec()
        changed["title"] = "different work"
        self.assertCode(
            "PLAN_CONFLICT",
            lambda: self.plan([changed, spec("clean", ["inspect"])], revision=1),
        )

    def test_completed_stage_cannot_be_deleted_or_weakened(self):
        self.plan()
        self.finish()
        self.assertCode("PLAN_CONFLICT", lambda: self.plan([spec("clean")], revision=1))
        self.assertCode(
            "PLAN_CONFLICT",
            lambda: self.plan(
                [spec(required=False), spec("clean", ["inspect"])], revision=1
            ),
        )

    def test_B04_duplicate_begin_returns_same_attempt_and_event(self):
        self.plan()
        first = self.begin(key="begin")
        seq = self.snapshot()["last_event_seq"]
        self.assertEqual(self.begin(key="begin"), first)
        self.assertEqual(self.snapshot()["last_event_seq"], seq)

    def test_B05_command_id_cannot_change_payload(self):
        self.plan()
        self.begin(key="begin")
        self.assertCode(
            "IDEMPOTENCY_CONFLICT", lambda: self.begin("clean", key="begin")
        )

    def test_B06_missing_artifact_never_completes(self):
        self.plan()
        a = self.begin()
        self.command(
            "report_result",
            {
                "stage_id": "inspect",
                **{k: a[k] for k in ("attempt_id", "execution_token")},
                "result": {},
                "artifact_candidates": [
                    {
                        "path": "missing.json",
                        "role": "report",
                        "slot": "report",
                        "media_type": "application/json",
                    }
                ],
            },
        )
        self.service.verify_pending()
        self.assertEqual(self.snapshot()["stages"][0]["status"], "failed")
        self.assertCode(
            "INVALID_TRANSITION",
            lambda: self.command(
                "complete_task",
                {"summary": "done", "artifact_refs": []},
                expected_plan_revision=1,
            ),
        )

    def test_serial_execution_and_dependency_gate(self):
        self.plan([spec(), spec("independent")])
        self.begin()
        self.assertCode("INVALID_TRANSITION", lambda: self.begin("independent"))

    def test_plan_cycle_unknown_capability_and_forged_state_rejected(self):
        self.assertCode(
            "INVALID_PLAN", lambda: self.plan([spec("a", ["b"]), spec("b", ["a"])])
        )
        s = spec()
        s["execution"]["capability_ref"] = "arbitrary-shell"
        self.assertCode("CAPABILITY_UNAVAILABLE", lambda: self.plan([s]))
        s = spec()
        s["status"] = "completed"
        self.assertCode("INVALID_PLAN", lambda: self.plan([s]))

    def test_output_reference_requires_dependency_and_verified_slot(self):
        s = spec("clean")
        s["input_refs"] = ["output:inspect:report"]
        self.assertCode("INVALID_PLAN", lambda: self.plan([spec(), s]))
        s["depends_on"] = ["inspect"]
        self.plan([spec(), s])
        self.finish()
        a = self.begin("clean")
        self.assertEqual(a["resolved_inputs"][0]["role"], "report")

    def test_B14_skip_optional_cannot_supply_required_output(self):
        self.plan([spec(required=False), spec("clean", ["inspect"])])
        self.command("skip_stage", {"stage_id": "inspect", "reason": "not needed"})
        self.assertEqual(self.snapshot()["stages"][0]["status"], "skipped")
        self.begin("clean")

    def test_S05_owner_isolation_and_S06_binding(self):
        other = Context("bob", "other-host", "session", "thread", "work")
        self.assertCode("NOT_FOUND", lambda: self.service.get_task(other, self.task))
        wrong = Context("alice", "host", "other", "other-thread", "work")
        self.assertCode(
            "FORBIDDEN",
            lambda: self.service.command(
                wrong, self.task, "request_replan", {"reason": "x"}, command_id="wrong"
            ),
        )

    def test_S07_artifact_traversal_and_symlink(self):
        self.plan()
        a = self.begin()
        for path in ("../secret", "/etc/passwd"):
            self.assertCode(
                "FORBIDDEN",
                lambda: self.command(
                    "report_result",
                    {
                        "stage_id": "inspect",
                        **{k: a[k] for k in ("attempt_id", "execution_token")},
                        "result": {},
                        "artifact_candidates": [
                            {
                                "path": path,
                                "role": "report",
                                "slot": "report",
                                "media_type": "text/plain",
                            }
                        ],
                    },
                ),
            )
        (self.root / "escape").symlink_to("/etc")
        self.assertCode(
            "FORBIDDEN", lambda: self.service.resolve_path("work", "escape/passwd")
        )

    def test_F11_control_CAS_and_stale_execution(self):
        self.plan()
        a = self.begin()
        self.assertCode(
            "VERSION_CONFLICT",
            lambda: self.command(
                "request_control",
                {"action": "pause"},
                ctx=self.user,
                expected_version=1,
            ),
        )
        self.command(
            "request_control",
            {"action": "cancel"},
            ctx=self.user,
            expected_version=self.snapshot()["task"]["version"],
        )
        self.assertCode(
            "STALE_EXECUTION",
            lambda: self.command(
                "report_result",
                {
                    "stage_id": "inspect",
                    **{k: a[k] for k in ("attempt_id", "execution_token")},
                    "result": {},
                    "artifact_candidates": [],
                },
            ),
        )

    def test_S04_agent_cannot_confirm_or_start_user_authority(self):
        self.assertCode(
            "AUTHORIZATION_REQUIRED",
            lambda: self.command(
                "request_control", {"action": "pause"}, expected_version=1
            ),
        )

    def test_persistence_and_receipt_survive_reopen(self):
        self.plan()
        a = self.begin(key="begin")
        snapshot = self.snapshot()
        self.service.close()
        self.service = TaskService(self.db, workspaces={"work": self.root})
        self.assertEqual(self.snapshot(), snapshot)
        self.assertEqual(self.begin(key="begin"), a)

    def test_F04_outbox_lease_fencing(self):
        first = self.service.storage.claim("first", now=100, lease_seconds=1)
        self.assertIsNotNone(first)
        second = self.service.storage.claim("second", now=102)
        self.assertEqual(first["id"], second["id"])
        self.assertCode(
            "STALE_EXECUTION",
            lambda: self.service.storage.ack(first["id"], first["fencing_token"]),
        )
        self.service.storage.ack(second["id"], second["fencing_token"])

    def test_B15_terminal_goal_requires_new_task(self):
        self.plan([spec()])
        self.finish()
        self.command(
            "complete_task",
            {
                "summary": "done",
                "artifact_refs": [a["id"] for a in self.snapshot()["artifacts"]],
            },
            expected_plan_revision=1,
        )
        self.assertCode(
            "INVALID_TRANSITION",
            lambda: self.command("request_replan", {"reason": "new goal"}),
        )


if __name__ == "__main__":
    unittest.main()
