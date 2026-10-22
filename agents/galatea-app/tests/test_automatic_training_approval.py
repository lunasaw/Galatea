"""Real signed Host calls and task transactions, without any training execution."""

from dataclasses import replace
from types import SimpleNamespace
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from codex_agent.app_gateway import AppGateway, ProofSigner
from codex_agent.app_integrations import AppIntegrations
from codex_agent.background_apps import BackgroundApps
from codex_agent.state import AtomicJsonStore
from galatea_app.api import AppAPI, RESOURCE
from galatea_app.trust import ProofVerifier
from galatea_app.workers import Worker
import test_domain
import test_training_authorization


FULL = {"approvalPolicy": "never", "sandboxPolicy": {"type": "dangerFullAccess"}}
AUTO = {"approvalPolicy": "on-request", "approvalsReviewer": "auto_review",
        "sandboxPolicy": {"type": "workspaceWrite"}}
ASK = {**AUTO, "approvalsReviewer": "user"}


class AutomaticTrainingApprovalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        b = self.b = test_training_authorization.TrainingAuthorizationTests()
        b.setUp()
        self.addCleanup(b.tearDown)
        self.addCleanup(b.doCleanups)
        self.adapter = b.install_policy()
        b.command("request_control", {"action": "cancel"}, ctx=b.user,
                  expected_version=b.snapshot()["task"]["version"])
        b.agent = replace(b.agent, call_id="chat-start")
        b.task = b.service.create(b.agent, command_id="chat-training", goal="smoke",
            constraints={"allow_training": True}, in_current_turn=True)["task_id"]
        stage = test_domain.spec("train", kind="training.run")
        stage["execution"]["capability_ref"] = "governed-training/v1"
        b.plan([stage])
        with b.service.storage.transaction() as db:
            db.execute("UPDATE outbox SET state='delivered'")
        self.session = {"id": "session", "thread_id": "thread", "principal_id": "alice",
                        "cwd": str(b.root), "settings": FULL, "status": "ready"}
        signer = ProofSigner(Ed25519PrivateKey.generate(), "key", "host", "alice")
        verifier = ProofVerifier({"key": {
            "public_key": signer.private_key.public_key(),
            "host_id": "host", "principal_id": "alice",
        }}, "galatea-app")
        api = AppAPI(b.service, verifier=verifier, credentials={})
        self.calls = []
        self.after_call = None

        async def native_call(method, params, key):
            self.assertEqual(method, "mcpServer/tool/call")
            self.calls.append((params, key))
            result = await api.rpc({"method": "tools/call", "params": {
                "name": params["tool"], "arguments": params["arguments"],
                "_meta": params["_meta"],
            }})
            if self.after_call:
                self.after_call(params)
            return result

        host = SimpleNamespace(
            store=AtomicJsonStore(b.root / "host"), principal_id="alice", workspace_dir=b.root,
            get_session=lambda id: self.session if id == "session" else None,
            _session_for_thread=lambda id: self.session if id == "thread" else None,
            native=SimpleNamespace(call=native_call),
        )
        self.integration = AppIntegrations.__new__(AppIntegrations)
        self.integration.host = host
        self.integration.gateway = AppGateway(host, signer,
            servers={"galatea_app": {"audience": "galatea-app", "resources": [RESOURCE],
                                      "tools": {"submit_input": {"read_only": False}}}},
            workspace_refs={str(b.root): "work"})
        self.integration.credentials = {"galatea-app": "test-token", "other": "other-token"}
        self.integration.background = BackgroundApps(host, workspaces={"work": b.root})
        self.integration.config = {"host_id": "host"}

        async def events(query):
            return {"events": [], "next_cursor": None}

        self.integration.events = SimpleNamespace(read_events=events)
        self.worker = Worker(b.service, SimpleNamespace(request=self.request))

    async def request(self, method, path, **kwargs):
        return await self.integration.route(method, path, kwargs.get("json", {}),
            {"authorization": "Bearer test-token"},
            {k: [v] for k, v in kwargs.get("params", {}).items()})

    def aggregate(self):
        with self.b.service.storage.lock:
            return self.b.service._load(self.b.service.storage.db, self.b.agent, self.b.task)

    async def approve(self):
        a = self.aggregate()
        await self.worker.approve_training(self.b.task, a["binding"], a["owner_id"])

    async def assert_automatic_execution_confirmation(self):
        proposed = self.b.propose()
        await self.worker.tick()
        await self.worker.tick()
        self.assertIsNone(self.worker.last_error)
        a = self.aggregate()
        self.assertEqual([p["arguments"]["values"]["decision"] for p, _ in self.calls],
                         ["authorize", "confirm"])
        self.assertEqual(a["snapshot"]["task"]["status"], "running")
        self.assertFalse(a["in_current_turn"])
        self.assertEqual(a["launch_receipt"], self.calls[-1][1])
        self.assertEqual(len(a["training_grants"]), 1)
        self.assertEqual(a["training_grants"][0]["proposal_digest"],
                         proposed["proposal"]["digest"])
        self.assertEqual(len(a["decisions"]), 1)
        self.assertIsNone(a["snapshot"]["stages"][0]["current_attempt"])
        self.assertEqual(a["snapshot"]["external_operations"], [])
        self.assertEqual(self.b.service.storage.db.execute(
            "SELECT COUNT(*) FROM outbox WHERE task_id=? AND kind='continuation'",
            (self.b.task,)).fetchone()[0], 1)
        for params, key in self.calls:
            interaction = params["_meta"]["io.codex.host/call-proof"]["claims"]["interaction"]
            self.assertEqual(interaction["approval_source"], "permission_profile")
            receipt = self.integration.gateway.host.store.read("app-calls/" + key + ".json")
            self.assertEqual(receipt["state"], "completed")

    async def test_full_access_authorizes_confirms_and_continues_once_without_app(self):
        await self.assert_automatic_execution_confirmation()

    async def test_auto_review_authorizes_confirms_and_continues_once_without_app(self):
        self.session["settings"] = AUTO
        await self.assert_automatic_execution_confirmation()

    async def test_ask_waits_without_mutation_and_mode_change_releases_pending_proposal(self):
        self.session["settings"] = ASK
        self.b.propose()
        before = self.aggregate()
        await self.worker.tick()
        self.assertEqual(self.aggregate(), before)
        self.assertEqual(self.calls, [])
        self.session["settings"] = FULL
        await self.worker.tick()
        self.assertEqual(self.b.snapshot()["task"]["status"], "running")
        self.assertEqual(len(self.calls), 2)

    async def test_switch_to_ask_between_decisions_preserves_grant_and_waits(self):
        self.b.propose()
        self.after_call = lambda params: self.session.update(settings=ASK)
        await self.approve()
        self.assertEqual(self.b.snapshot()["task"]["status"], "waiting_input")
        self.assertEqual(len(self.calls), 1)
        await self.approve()
        self.assertEqual(len(self.calls), 1)
        self.after_call = None
        self.session["settings"] = AUTO
        await self.approve()
        self.assertEqual(self.b.snapshot()["task"]["status"], "running")
        self.assertEqual(len(self.calls), 2)

    async def test_scope_budget_and_readiness_still_block_execution(self):
        self.b.proposal["resources"]["gpu"] = 2
        self.b.propose()
        await self.approve()
        self.assertEqual(self.calls, [])
        self.b.proposal["resources"]["gpu"] = 1
        self.b.proposal["readiness"] = [{"id": "data", "status": "pending"}]
        self.b.propose()
        await self.approve()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.b.snapshot()["task"]["status"], "waiting_input")
        self.assertEqual(self.calls[0][0]["arguments"]["values"]["decision"], "authorize")

    async def test_replacement_between_authorize_and_confirm_does_not_confirm_old_proposal(self):
        old = self.b.propose()
        self.after_call = lambda params: self.b.propose()
        await self.approve()
        self.assertEqual(len(self.calls), 1)
        a = self.aggregate()
        self.assertEqual(a["requests"][old["request"]["request_id"]]["status"], "superseded")
        self.assertEqual(a["decisions"], [])
        self.after_call = None
        await self.approve()
        self.assertEqual(len(self.calls), 3)
        self.assertNotEqual(self.calls[-1][0]["arguments"]["values"]["proposal_digest"],
                            old["proposal"]["digest"])

    async def test_pause_between_decisions_stops_automatic_confirmation(self):
        self.b.propose()
        self.after_call = lambda params: self.b.command(
            "request_control", {"action": "pause"}, ctx=self.b.user,
            expected_version=self.b.snapshot()["task"]["version"])
        await self.approve()
        await self.approve()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.b.snapshot()["task"]["status"], "paused")

    async def test_deferred_and_expired_proposals_are_not_confirmed(self):
        old = self.b.propose()
        self.b.decide(old, "defer")
        await self.approve()
        self.assertEqual(self.calls, [])
        # Expiry and replacement are checked from authoritative task facts.
        with self.b.service.storage.transaction() as db:
            a = self.b.service._load(db, self.b.agent, self.b.task)
            a["snapshot"]["task"]["status"] = "waiting_approval"
            a["requests"][old["request"]["request_id"]]["status"] = "open"
            a["proposals"][old["proposal"]["proposal_id"]]["expires_at"] = "2000-01-01T00:00:00Z"
            self.b.service._save(db, a)
        await self.approve()
        self.assertEqual(self.calls, [])

    async def test_internal_endpoint_requires_authentication_and_matching_audience(self):
        body = {"server": "galatea_app", "uri": RESOURCE, "host_session_id": "session",
                "name": "submit_input", "arguments": {}}
        for headers in ({}, {"authorization": "Bearer other-token"}):
            with self.assertRaises(PermissionError):
                await self.integration.route("POST", "/internal/v1/app-tool-calls", body, headers, {})
        with self.assertRaises(ValueError):
            await self.request("POST", "/internal/v1/app-tool-calls",
                               json={**body, "host_session_id": "other"})
        self.assertEqual(self.calls, [])
