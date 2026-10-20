"""User grants require exact Host decisions and survive service restart."""

import copy
from dataclasses import replace

from galatea_app.integrations.project_governance import ProjectGovernance
from galatea_app.service import TaskService
from test_training import TrainingTests


class TrainingAuthorizationTests(TrainingTests):
    def setUp(self):
        super().setUp()
        self.policy = {
            "principal_ids": ["alice"],
            "roles": ["smoke"],
            "allow_final_test": False,
            "resources": {"gpu": 1, "cpu": 2},
            "hard_limits": copy.deepcopy(self.proposal["hard_limits"]),
            "expires_at": "2099-01-02T00:00:00Z",
        }

    def install_policy(self):
        adapter = ProjectGovernance(
            project_id="fixture", profile="test", ray_api="http://ray:8265",
            tracking_uri="http://mlflow:5000", receipts_root=self.root / "receipts",
            project_root=self.root, user_authorization=self.policy,
        )
        self.addCleanup(adapter.http.close)
        self.service.governance = {"project_governance": adapter}
        return adapter

    def availability(self, proposal):
        return self.service.get_context(self.agent, self.task)[
            "training_decisions"
        ][proposal["proposal"]["proposal_id"]]

    def test_user_grant_survives_restart_and_does_not_start_training(self):
        self.install_policy()
        proposed = self.propose()
        self.assertTrue(self.availability(proposed)["can_authorize"])
        with self.service.storage.lock:
            before = self.service.storage.db.execute(
                "SELECT COUNT(*) FROM outbox WHERE task_id=?", (self.task,)
            ).fetchone()[0]
        granted = self.decide(proposed, "authorize", key="authorize")
        self.assertEqual(granted["result"]["state"], "authorized")
        self.assertEqual(self.snapshot()["task"]["status"], "waiting_input")
        self.assertIsNone(self.snapshot()["stages"][0]["current_attempt"])
        with self.service.storage.lock:
            self.assertEqual(before, self.service.storage.db.execute(
                "SELECT COUNT(*) FROM outbox WHERE task_id=?", (self.task,)
            ).fetchone()[0])
        self.service.close()
        self.service = TaskService(self.db, workspaces={"work": self.root})
        adapter = self.install_policy()
        self.assertTrue(self.availability(proposed)["can_confirm"])
        self.assertCode("INVALID_TRANSITION", lambda: self.begin("train"))
        self.decide(proposed)
        self.assertTrue(self.begin("train")["attempt_id"])
        adapter.user_authorization["principal_ids"] = []
        with self.service.storage.lock:
            a = self.service._load(self.service.storage.db, self.agent, self.task)
            self.assertCode("AUTHORIZATION_REQUIRED",
                            lambda: self.service._authorized(a, proposed["proposal"]))

    def test_authorize_requires_trusted_user_and_exact_digest(self):
        self.install_policy()
        proposed = self.propose()
        payload = {"request_id": proposed["request"]["request_id"], "values": {
            "decision": "authorize", "proposal_digest": proposed["proposal"]["digest"]}}
        for ctx in (self.agent, replace(self.user, confirmed=False),
                    replace(self.user, interaction_id="")):
            self.assertCode("AUTHORIZATION_REQUIRED", lambda: self.command(
                "submit_input", payload, ctx=ctx,
                expected_version=self.snapshot()["task"]["version"]))
        changed = copy.deepcopy(payload)
        changed["values"]["proposal_digest"] = "wrong"
        self.assertCode("PLAN_CONFLICT", lambda: self.command(
            "submit_input", changed, ctx=self.user,
            expected_version=self.snapshot()["task"]["version"]))
        self.decide(proposed, "authorize")
        self.assertCode("AUTHORIZATION_REQUIRED", lambda: self.decide(proposed, "authorize"))

    def test_policy_denies_foreign_or_expanded_scope(self):
        adapter = self.install_policy()
        self.assertTrue(adapter.can_authorize("alice", self.proposal))
        self.assertFalse(adapter.can_authorize("someone-else", self.proposal))
        changes = [
            ("immutable_inputs", "project_id", "other"),
            ("immutable_inputs", "profile", "prod"),
            ("immutable_inputs", "role", "champion"),
            ("immutable_inputs", "final_test", True),
            ("resources", "gpu", 2),
            ("resources", "cpu", 3),
            ("hard_limits", "attempts", 2),
            ("hard_limits", "retries", 1),
            ("hard_limits", "wall_seconds", 601),
            ("hard_limits", "memory_bytes", 1025),
        ]
        for section, key, value in changes:
            with self.subTest(section=section, key=key):
                changed = copy.deepcopy(self.proposal)
                changed[section][key] = value
                self.assertFalse(adapter.can_authorize("alice", changed))
        self.policy["expires_at"] = "2000-01-01T00:00:00Z"
        self.assertFalse(adapter.can_authorize("alice", self.proposal))

    def test_reproposal_invalidates_grant_even_with_same_inputs(self):
        self.install_policy()
        old = self.propose()
        self.decide(old, "authorize")
        new = self.propose()
        self.assertFalse(self.availability(new)["authorized"])
        self.assertTrue(self.availability(new)["can_authorize"])
        self.assertCode("VERSION_CONFLICT", lambda: self.decide(old))
        self.assertCode("AUTHORIZATION_REQUIRED", lambda: self.decide(new))
        self.decide(new, "authorize")
        self.decide(new)
        self.assertTrue(self.begin("train")["attempt_id"])

    def test_missing_adapter_and_delegation_have_actionable_reasons(self):
        self.service.governance = {}
        proposed = self.propose()
        self.assertFalse(self.availability(proposed)["can_authorize"])
        self.assertIn("尚未配置", self.availability(proposed)["reason"])
        adapter = self.install_policy()
        self.assertTrue(self.availability(proposed)["can_authorize"])
        adapter.user_authorization = {}
        self.assertFalse(self.availability(proposed)["can_authorize"])
        self.assertIn("权限", self.availability(proposed)["reason"])

    def test_stale_version_and_defer_never_grant(self):
        self.install_policy()
        proposed = self.propose()
        self.assertCode("VERSION_CONFLICT", lambda: self.command("submit_input", {
            "request_id": proposed["request"]["request_id"], "values": {
                "decision": "authorize", "proposal_digest": proposed["proposal"]["digest"]}},
            ctx=self.user, expected_version=0))
        self.decide(proposed, "defer")
        self.assertFalse(self.availability(proposed)["can_authorize"])
        self.assertCode("AUTHORIZATION_REQUIRED", lambda: self.decide(proposed, "authorize"))

    def test_pending_readiness_is_not_waived_by_user_authorization(self):
        self.install_policy()
        self.proposal["readiness"] = [{"id": "memory", "label": "内存限制", "status": "pending"}]
        proposed = self.propose()
        self.assertTrue(self.availability(proposed)["can_authorize"])
        self.assertIn("内存限制", self.availability(proposed)["reason"])
        self.assertIn("尚未授权", self.availability(proposed)["confirm_reason"])
        self.assertIn("内存限制", self.availability(proposed)["confirm_reason"])
        self.decide(proposed, "authorize")
        self.assertFalse(self.availability(proposed)["can_confirm"])
        self.assertIn("内存限制", self.availability(proposed)["reason"])
        self.assertCode("AUTHORIZATION_REQUIRED", lambda: self.decide(proposed))

    def test_expired_proposal_explains_why_authorization_is_unavailable(self):
        from unittest.mock import patch

        self.install_policy()
        proposed = self.propose()
        with patch("galatea_app.service.time.time", return_value=5_000_000_000):
            state = self.availability(proposed)
            self.assertFalse(state["can_authorize"])
            self.assertFalse(state["can_confirm"])
            self.assertIn("过期", state["confirm_reason"])
            self.assertNotIn("请先完成", state["confirm_reason"])

    def test_exact_delegation_and_authorization_replay(self):
        adapter = self.install_policy()
        self.policy["immutable_inputs"] = copy.deepcopy(self.proposal["immutable_inputs"])
        changed = copy.deepcopy(self.proposal)
        changed["immutable_inputs"]["release_digest"] = "another-release"
        self.assertFalse(adapter.can_authorize("alice", changed))
        proposed = self.propose()
        version = self.snapshot()["task"]["version"]
        payload = {"request_id": proposed["request"]["request_id"], "values": {
            "decision": "authorize", "proposal_digest": proposed["proposal"]["digest"]}}
        first = self.command("submit_input", payload, ctx=self.user,
                             key="stable-grant", expected_version=version)
        second = self.command("submit_input", payload, ctx=self.user,
                              key="stable-grant", expected_version=version)
        self.assertEqual(first, second)
        with self.service.storage.lock:
            a = self.service._load(self.service.storage.db, self.agent, self.task)
            self.assertEqual(len(a["training_grants"]), 1)

    def test_scheduling_only_memory_requires_disclosure_and_explicit_delegation(self):
        adapter = self.install_policy()
        self.proposal["hard_limits"]["memory_bytes"] = None
        self.assertCode("INVALID_COMMAND", self.propose)
        self.proposal["resources"]["memory"]["enforcement"] = "scheduling_only"
        self.proposal["estimate"]["memory_limit_enforcement"] = (
            "仅调度预留，没有每个作业的内存硬上限"
        )
        proposed = self.propose()
        self.assertFalse(self.availability(proposed)["can_authorize"])
        self.assertCode("AUTHORIZATION_REQUIRED",
                        lambda: self.decide(proposed, "authorize"))
        adapter.user_authorization["hard_limits"]["memory_bytes"] = None
        self.assertFalse(self.availability(proposed)["can_authorize"])
        adapter.user_authorization["allow_unbounded_memory"] = True
        self.assertTrue(self.availability(proposed)["can_authorize"])
        self.decide(proposed, "authorize")
        self.assertTrue(self.availability(proposed)["can_confirm"])
        self.assertIsNone(self.snapshot()["stages"][0]["current_attempt"])

    def test_button_availability_survives_native_mcp_redaction(self):
        from codex_agent.console.projection import redact
        from galatea_app.projections import Workbench

        self.install_policy()
        proposed = self.propose()
        view = Workbench(self.service).view(self.user, self.task)
        public, _ = redact({"structuredContent": view}, method="mcpServer/tool/call")
        availability = public["structuredContent"]["training_decisions"][
            proposed["proposal"]["proposal_id"]]
        self.assertIs(availability["can_authorize"], True)
        self.assertIs(availability["can_confirm"], False)


# Reuse the training fixture without collecting its unrelated inherited cases twice.
for name in list(vars(TrainingTests)):
    if name.startswith("test_") and name not in vars(TrainingAuthorizationTests):
        setattr(TrainingAuthorizationTests, name, None)
del TrainingTests
