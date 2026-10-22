import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from codex_agent.app_gateway import AppGateway, ProofSigner
from codex_agent.state import AtomicJsonStore
from galatea_app.trust import ProofVerifier


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.calls = []
        self.session = {
            "id": "session",
            "thread_id": "thread",
            "principal_id": "alice",
            "cwd": str(self.root),
        }

        async def call(method, params, key):
            self.calls.append(params)
            return {"content": [{"type": "text", "text": "ok"}]}

        host = SimpleNamespace(
            store=AtomicJsonStore(self.root / "state"),
            principal_id="alice",
            workspace_dir=self.root,
            _session_for_thread=lambda id: self.session if id == "thread" else None,
            native=SimpleNamespace(call=call),
        )
        self.gateway = AppGateway(
            host,
            ProofSigner(Ed25519PrivateKey.generate(), "key", "host", "alice"),
            servers={
                "app": {
                    "audience": "audience",
                    "resources": ["ui://app/work"],
                    "tools": {
                        "read": {"read_only": True},
                        "write": {"read_only": False},
                    },
                }
            },
            workspace_refs={str(self.root): "work"},
        )
        self.body = {
            "server": "app",
            "uri": "ui://app/work",
            "instanceId": "frame",
            "threadId": "thread",
            "name": "write",
            "arguments": {"command_id": "command"},
            "_meta": {"io.codex.host/call-proof": "forged"},
        }

    def tearDown(self):
        self.tmp.cleanup()

    async def test_challenge_binds_arguments_and_discards_forged_proof(self):
        challenge = self.gateway.challenge(self.body)
        with self.assertRaises(ValueError):
            await self.gateway.call({"call_id": challenge["call_id"]})
        self.gateway.confirm({**challenge, "decision": "confirmed"})
        await self.gateway.call({"call_id": challenge["call_id"]})
        await self.gateway.call({"call_id": challenge["call_id"]})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            self.calls[0]["_meta"]["io.codex.host/call-proof"]["claims"][
                "principal_id"
            ],
            "alice",
        )

    async def test_permission_profiles_control_app_confirmation(self):
        for settings in (
            {"approvalPolicy": "never", "sandboxPolicy": {"type": "dangerFullAccess"}},
            {"approvalPolicy": "on-request", "approvalsReviewer": "auto_review",
             "sandboxPolicy": {"type": "workspaceWrite"}},
            {"approvalPolicy": "on-request", "approvalsReviewer": "guardian_subagent",
             "sandbox": "workspace-write"},
        ):
            self.session["settings"] = settings
            body = {**self.body, "arguments": {"profile": settings}}
            challenge = self.gateway.challenge(body)
            self.assertEqual(challenge["state"], "confirmed")
            await self.gateway.call({"call_id": challenge["call_id"]})
            await self.gateway.call({"call_id": challenge["call_id"]})
            proof = self.calls[-1]["_meta"]["io.codex.host/call-proof"]
            verifier = ProofVerifier({"key": {
                "public_key": self.gateway.signer.private_key.public_key(),
                "host_id": "host", "principal_id": "alice",
            }}, "audience")
            context = verifier.verify(proof, "write", body["arguments"], write=True)
            self.assertTrue(context.confirmed)
            self.assertTrue(context.interaction_id)
            self.assertEqual(proof["claims"]["interaction"]["approval_source"], "permission_profile")
        self.assertEqual(len(self.calls), 3)

        self.session["settings"] = {"approvalPolicy": "on-request", "approvalsReviewer": "user"}
        body = {**self.body, "arguments": {"profile": "ask"}}
        challenge = self.gateway.challenge(body)
        self.assertEqual(challenge["state"], "pending")
        with self.assertRaises(ValueError):
            await self.gateway.call({"call_id": challenge["call_id"]})

    async def test_app_cannot_select_automatic_confirmation_and_custom_modes_still_ask(self):
        for i, settings in enumerate((
            {}, {"approvalPolicy": "never", "sandbox": "read-only"},
            {"approvalPolicy": "on-request", "approvalsReviewer": "auto_review",
             "sandboxPolicy": {"type": "workspaceWrite", "networkAccess": True}},
            {"approvalPolicy": "never", "sandbox": "danger-full-access", "permissions": "custom"},
        )):
            self.session["settings"] = settings
            challenge = self.gateway.challenge({**self.body,
                "arguments": {"case": i},
                "settings": {"approvalPolicy": "never", "sandbox": "danger-full-access"},
                "approval_source": "permission_profile"})
            self.assertEqual(challenge["state"], "pending")
        self.assertEqual(self.calls, [])

    async def test_permission_changes_recheck_pending_calls_without_overriding_denial(self):
        challenge = self.gateway.challenge(self.body)
        self.session["settings"] = {"approvalPolicy": "never", "sandbox": "danger-full-access"}
        repeated = self.gateway.challenge(self.body)
        self.assertEqual(repeated["call_id"], challenge["call_id"])
        self.assertEqual(repeated["state"], "confirmed")
        self.session["settings"] = {"approvalPolicy": "on-request", "sandbox": "workspace-write"}
        with self.assertRaisesRegex(ValueError, "confirmation required"):
            await self.gateway.call({"call_id": challenge["call_id"]})
        self.assertEqual(self.calls, [])
        self.gateway.confirm({**challenge, "decision": "denied"})
        self.session["settings"] = {"approvalPolicy": "never", "sandbox": "danger-full-access"}
        self.assertEqual(self.gateway.challenge(self.body)["state"], "denied")
        with self.assertRaises(ValueError):
            await self.gateway.call({"call_id": challenge["call_id"]})

    async def test_active_turn_permissions_override_thread_defaults(self):
        self.session["settings"] = {"approvalPolicy": "never", "sandboxPolicy": {"type": "dangerFullAccess"}}
        self.session["turn_settings"] = {"approvalPolicy": "on-request",
            "approvalsReviewer": "user", "sandboxPolicy": {"type": "workspaceWrite"}}
        self.assertEqual(self.gateway.challenge(self.body)["state"], "pending")
        self.session["turn_settings"]["approvalsReviewer"] = "auto_review"
        self.assertEqual(self.gateway.challenge(self.body)["state"], "confirmed")

    async def test_read_refresh_does_not_replay_cached_business_snapshot(self):
        self.body["name"] = "read"
        first = self.gateway.challenge(self.body)
        second = self.gateway.challenge(self.body)
        self.assertNotEqual(first["call_id"], second["call_id"])

    async def test_wrong_server_or_thread_rejected(self):
        with self.assertRaises(ValueError):
            self.gateway.challenge({**self.body, "server": "other"})
        with self.assertRaises(ValueError):
            self.gateway.challenge({**self.body, "threadId": "other"})

    async def test_nested_directory_uses_most_specific_registered_workspace(self):
        self.session["cwd"] = str(self.root / "projects" / "example")
        self.assertEqual(self.gateway._scope(self.body)[2], "work")
        self.gateway.workspace_refs[str(self.root / "projects")] = "projects"
        self.assertEqual(self.gateway._scope(self.body)[2], "projects")

    async def test_outside_directory_and_symlink_escape_are_rejected(self):
        self.session["cwd"] = str(self.root.parent)
        with self.assertRaisesRegex(ValueError, "workspace is not registered"):
            self.gateway._scope(self.body)
        (self.root / "escape").symlink_to(self.root.parent, target_is_directory=True)
        self.session["cwd"] = str(self.root / "escape")
        with self.assertRaisesRegex(ValueError, "workspace is not registered"):
            self.gateway._scope(self.body)

    async def test_unknown_native_call_never_blind_retries(self):
        async def fail(*args):
            self.calls.append(args)
            raise TimeoutError()

        self.gateway.host.native.call = fail
        challenge = self.gateway.challenge(self.body)
        self.gateway.confirm({**challenge, "decision": "confirmed"})
        with self.assertRaises(TimeoutError):
            await self.gateway.call({"call_id": challenge["call_id"]})
        with self.assertRaises(ValueError):
            await self.gateway.call({"call_id": challenge["call_id"]})
        self.assertEqual(len(self.calls), 1)
