import copy
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from galatea_app.common import TaskError
from galatea_app.trust import ProofVerifier
from galatea_app.projections import Workbench
from codex_agent.app_gateway import ProofSigner
from test_domain import DomainTests


class TrustTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.signer = ProofSigner(self.key, "key-1", "host", "alice")
        self.verifier = ProofVerifier(
            {
                "key-1": {
                    "public_key": self.key.public_key(),
                    "host_id": "host",
                    "principal_id": "alice",
                }
            },
            "galatea-app",
        )
        self.args = {"task_id": "task", "command_id": "command", "action": "cancel"}

    def proof(self, **changes):
        fields = dict(
            audience="galatea-app",
            session_id="session",
            thread_id="thread",
            workspace_ref="work",
            app_instance_id="app",
            tool="request_control",
            arguments=self.args,
            call_id="call",
            interaction={
                "id": "confirmation",
                "decision": "confirmed",
                "arguments_digest": None,
            },
            now=100,
        )
        fields.update(changes)
        return self.signer.sign_call(**fields)

    def test_S01_trusted_identity_is_not_tool_arguments(self):
        ctx = self.verifier.verify(
            self.proof(), "request_control", self.args, write=True, now=101
        )
        self.assertEqual(ctx.principal_id, "alice")
        self.assertTrue(ctx.confirmed)

    def test_S02_signature_tamper_and_unknown_key(self):
        p = self.proof()
        p["claims"]["principal_id"] = "bob"
        with self.assertRaises(TaskError):
            self.verifier.verify(p, "request_control", self.args, write=True, now=101)
        p = self.proof()
        p["key_id"] = "unknown"
        with self.assertRaises(TaskError):
            self.verifier.verify(p, "request_control", self.args, write=True, now=101)

    def test_S03_tool_audience_parameters_expiry(self):
        for changes, tool, args, now in [
            ({}, "create_task", self.args, 101),
            ({}, "request_control", {**self.args, "action": "resume"}, 101),
            ({"audience": "other"}, "request_control", self.args, 101),
            ({}, "request_control", self.args, 1000),
        ]:
            with self.subTest(changes=changes, tool=tool, now=now), self.assertRaises(
                TaskError
            ):
                self.verifier.verify(
                    self.proof(**changes), tool, args, write=True, now=now
                )

    def test_S04_write_without_confirmation_rejected(self):
        with self.assertRaises(TaskError):
            self.verifier.verify(
                self.proof(interaction=None),
                "request_control",
                self.args,
                write=True,
                now=101,
            )


class ProjectionTests(DomainTests):
    def test_B07_B08_native_completion_and_plan_never_complete_business(self):
        self.plan()
        self.begin()
        w = Workbench(self.service)
        for seq, type in [(1, "turn/completed"), (2, "turn/plan/updated")]:
            w.ingest(
                self.task,
                {
                    "host_id": "host",
                    "epoch": "epoch",
                    "seq": seq,
                    "session_id": "session",
                    "thread_id": "thread",
                    "type": type,
                    "turn_id": "turn",
                    "content": "done",
                },
            )
        self.assertEqual(self.snapshot()["stages"][0]["status"], "running")
        self.assertEqual(self.snapshot()["task"]["plan_revision"], 1)

    def test_message_stream_updates_same_item_with_independent_cursor(self):
        w = Workbench(self.service)
        event = {
            "host_id": "host",
            "epoch": "epoch",
            "seq": 1,
            "session_id": "session",
            "thread_id": "thread",
            "type": "public_message",
            "message_id": "message",
            "role": "assistant",
            "safe_content": "first",
            "content_revision": 1,
        }
        w.ingest(self.task, event)
        first = w.events(self.agent, self.task)
        event.update(seq=2, safe_content="first and second", content_revision=2)
        w.ingest(self.task, event)
        w.ingest(self.task, event)
        delta = w.events(self.agent, self.task, after_cursor=first["next_cursor"])
        self.assertEqual(len(delta["items"]), 1)
        self.assertEqual(delta["items"][0]["item"]["safe_content"], "first and second")
        self.assertEqual(
            len(w.view(self.agent, self.task)["timeline_page"]["items"]), 1
        )

    def test_latest_timeline_and_preview_redaction(self):
        w = Workbench(self.service)
        for n in range(105):
            w.ingest(
                self.task,
                {
                    "host_id": "host",
                    "epoch": "epoch",
                    "seq": n + 1,
                    "session_id": "session",
                    "thread_id": "thread",
                    "type": "public_message",
                    "message_id": str(n),
                    "role": "assistant",
                    "safe_content": str(n),
                },
            )
        page = w.view(self.agent, self.task)["timeline_page"]
        self.assertEqual(page["items"][-1]["safe_content"], "104")
        self.assertTrue(page["has_earlier"])
        self.plan()
        attempt = self.begin()
        change = w.capture_change(
            self.agent,
            self.task,
            stage_id="inspect",
            attempt_id=attempt["attempt_id"],
            relative_path="config.txt",
            before="",
            after="api_key=private-value",
        )
        self.assertNotIn(
            "private-value",
            str(w.file_change(self.agent, self.task, change["change_id"])),
        )
        (self.root / "report.json").write_text('{"token":"private-value"}')
        self.command(
            "report_result",
            {
                **attempt,
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
        self.service.verify_pending()
        self.assertNotIn(
            "private-value",
            self.service.artifact(
                self.agent, self.task, self.snapshot()["artifacts"][0]["id"]
            )["content"],
        )

    def test_reasoning_is_never_projected_and_gaps_visible(self):
        w = Workbench(self.service)
        w.ingest(
            self.task,
            {
                "host_id": "host",
                "epoch": "epoch",
                "seq": 3,
                "session_id": "session",
                "thread_id": "thread",
                "type": "reasoning",
                "content": "private chain",
            },
        )
        view = w.view(self.agent, self.task)
        self.assertNotIn("private chain", str(view))
        self.assertEqual(view["source_cursors"]["host"]["coverage"], "incomplete")

    def test_file_diff_is_immutable_and_owner_scoped(self):
        self.plan()
        a = self.begin()
        w = Workbench(self.service)
        change = w.capture_change(
            self.agent,
            self.task,
            stage_id="inspect",
            attempt_id=a["attempt_id"],
            relative_path="report.json",
            before='{"rows":1}\n',
            after='{"rows":2}\n',
        )
        (self.root / "report.json").write_text("unrelated later edit")
        result = w.file_change(self.agent, self.task, change["change_id"])
        self.assertIn('-{"rows":1}', "\n".join(result["hunks"]))
        self.assertIn('+{"rows":2}', "\n".join(result["hunks"]))
        self.assertNotIn("unrelated", str(result))


for name in list(vars(DomainTests)):
    if name.startswith("test_") and name not in vars(ProjectionTests):
        setattr(ProjectionTests, name, None)
del DomainTests
