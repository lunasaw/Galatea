import json
import unittest
from test_domain import DomainTests, spec
from galatea_app.service import Context
from galatea_app.common import TaskError


class HardeningTests(DomainTests):
    def test_agent_draft_cannot_start_without_host_confirmation(self):
        self.command(
            "request_control", {"action": "cancel"}, ctx=self.user, expected_version=1
        )
        self.task = self.service.create(
            self.agent, command_id="draft", goal="draft only", start=False
        )["task_id"]
        self.plan([spec()])
        self.assertCode("AUTHORIZATION_REQUIRED", lambda: self.begin())

    def test_malformed_stage_returns_domain_error_and_preserves_task(self):
        self.assertCode("INVALID_PLAN", lambda: self.plan(["not-json"]))
        self.assertEqual(self.snapshot()["task"]["plan_revision"], 0)

    def test_unresolved_prepared_intent_blocks_fail_task(self):
        self.plan([spec()])
        # Storage crash fixture: submission intent was durable before a response was lost.
        with self.service.storage.transaction() as db:
            a = self.service._load(db, self.agent, self.task)
            a["intents"]["pending"] = {"state": "unknown", "attempt_id": "unreturned"}
            self.service._save(db, a)
        self.assertCode(
            "EXECUTION_UNKNOWN",
            lambda: self.command(
                "fail_task", {"reason_code": "give-up", "evidence_refs": []}
            ),
        )

    def test_restore_backup_does_not_claim_old_outbox(self):
        self.service.storage.backup(self.root / "restored.sqlite3")
        from galatea_app.storage import Storage

        restored = Storage(self.root / "restored.sqlite3")
        self.assertIsNone(restored.claim("new-worker", now=9999999999))
        restored.close()

    def test_artifact_snapshot_does_not_change_after_workspace_edit(self):
        self.plan([spec()])
        self.finish()
        artifact = self.snapshot()["artifacts"][0]
        before = self.service.artifact(self.user, self.task, artifact["id"])
        (self.root / "report.json").write_text("changed")
        self.assertEqual(
            self.service.artifact(self.user, self.task, artifact["id"]), before
        )


for name in list(vars(DomainTests)):
    if name.startswith("test_") and name not in vars(HardeningTests):
        setattr(HardeningTests, name, None)
del DomainTests


class FileSnapshotTests(unittest.TestCase):
    def test_code_stage_records_before_and_after_not_later_workspace(self):
        import test_domain

        base = test_domain.DomainTests()
        base.setUp()
        try:
            (base.root / "module.py").write_text("value = 1\n")
            s = spec(kind="code.prepare")
            base.plan([s])
            a = base.begin()
            (base.root / "module.py").write_text("value = 2\n")
            base.finish(attempt=a)
            from galatea_app.projections import Workbench

            w = Workbench(base.service)
            details = w.stage_details(base.user, base.task, "inspect")
            changes = [
                c for c in details["file_changes"] if c["relative_path"] == "module.py"
            ]
            self.assertEqual(len(changes), 1)
            (base.root / "module.py").write_text("value = 3\n")
            diff = w.file_change(base.user, base.task, changes[0]["change_id"])
            self.assertIn("-value = 1", diff["hunks"])
            self.assertIn("+value = 2", diff["hunks"])
        finally:
            base.tearDown()


class PauseDrainTests(unittest.TestCase):
    def test_pause_blocks_new_work_but_accepts_draining_result(self):
        import test_domain

        base = test_domain.DomainTests()
        base.setUp()
        try:
            base.plan()
            attempt = base.begin()
            base.command(
                "request_control",
                {"action": "pause"},
                ctx=base.user,
                expected_version=base.snapshot()["task"]["version"],
            )
            base.finish(attempt=attempt)
            self.assertEqual(base.snapshot()["stages"][0]["status"], "completed")
            self.assertEqual(base.snapshot()["task"]["status"], "paused")
            base.assertCode("INVALID_TRANSITION", lambda: base.begin("clean"))
        finally:
            base.tearDown()


class VerificationFailures(unittest.TestCase):
    def test_invalid_json_schema_is_rejected_without_killing_verifier(self):
        import test_domain

        b = test_domain.DomainTests()
        b.setUp()
        try:
            stage = spec()
            stage["acceptance"].append(
                {
                    "id": "schema",
                    "type": "schema_matches",
                    "parameters": {
                        "artifact_role": "report",
                        "schema_ref": "object/v1",
                    },
                }
            )
            b.plan([stage])
            a = b.begin()
            (b.root / "list.json").write_text("[]")
            b.command(
                "report_result",
                {
                    "stage_id": "inspect",
                    "attempt_id": a["attempt_id"],
                    "execution_token": a["execution_token"],
                    "result": {},
                    "artifact_candidates": [
                        {
                            "path": "list.json",
                            "slot": "report",
                            "role": "report",
                            "media_type": "application/json",
                        }
                    ],
                },
            )
            b.service.verify_pending()
            self.assertEqual(b.snapshot()["stages"][0]["status"], "failed")
        finally:
            b.tearDown()
