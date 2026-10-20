import json
from pathlib import Path
import tempfile
import unittest
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from codex_agent.app_gateway import ProofSigner, AppGateway
from codex_agent.state import AtomicJsonStore
from galatea_app.api import AppAPI
from galatea_app.service import TaskService, Context
from galatea_app.trust import ProofVerifier


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.key = Ed25519PrivateKey.generate()
        self.service = TaskService(
            self.root / "task.db", workspaces={"work": self.root}
        )
        self.signer = ProofSigner(self.key, "k", "host", "alice")
        self.api = AppAPI(
            self.service,
            verifier=ProofVerifier(
                {
                    "k": {
                        "public_key": self.key.public_key(),
                        "host_id": "host",
                        "principal_id": "alice",
                    }
                },
                "app",
            ),
            credentials={
                "mcp": {"token": "x" * 32, "role": "mcp"},
                "provider": {
                    "token": "y" * 32,
                    "role": "provider",
                    "host_id": "host",
                    "principal_id": "alice",
                },
            },
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.api),
            base_url="http://test",
            headers={"Authorization": "Bearer " + "x" * 32},
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.service.close()
        self.tmp.cleanup()

    async def rpc(self, name, args, proof=None):
        r = await self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": args,
                    "_meta": {"io.codex.host/call-proof": proof},
                },
            },
        )
        return r.json()["result"]

    def sign(self, name, args, write=True):
        return self.signer.sign_call(
            audience="app",
            session_id="session",
            thread_id="thread",
            workspace_ref="work",
            app_instance_id="frame",
            tool=name,
            arguments=args,
            call_id="call-" + args.get("command_id", "read"),
            interaction={"id": "confirmed", "decision": "confirmed"} if write else None,
        )

    async def test_untrusted_reads_and_model_writes_fail_closed(self):
        result = await self.rpc("list_tasks", {})
        self.assertTrue(result["isError"])
        result = await self.rpc("create_task", {"goal": "work", "command_id": "c"})
        self.assertTrue(result["isError"])
        r = await self.client.post(
            "/mcp", headers={"Authorization": "Bearer invalid"}, json={}
        )
        self.assertEqual(r.status_code, 401)

    async def test_signed_mcp_create_read_and_idempotent_retry(self):
        args = {"goal": "clean data", "command_id": "c", "start": True}
        first = await self.rpc("create_task", args, self.sign("create_task", args))
        second = await self.rpc("create_task", args, self.sign("create_task", args))
        self.assertEqual(first, second)
        args = {"task_id": first["structuredContent"]["task_id"]}
        view = await self.rpc(
            "get_workbench", args, self.sign("get_workbench", args, False)
        )
        self.assertEqual(
            view["structuredContent"]["task_snapshot"]["task"]["status"], "planning"
        )

    async def test_structured_stage_and_sha256_report_contract(self):
        import hashlib
        from test_domain import spec

        user = Context(
            "alice",
            "host",
            "session",
            "thread",
            "work",
            role="app",
            confirmed=True,
            interaction_id="test",
        )
        agent = Context("alice", "host", "session", "thread", "work")
        task = self.service.create(
            user, command_id="create-sha", goal="inspect", start=True
        )["task_id"]
        await self.api.dispatch(
            agent,
            "replace_plan",
            {
                "task_id": task,
                "command_id": "plan",
                "expected_plan_revision": 0,
                "reason": "check",
                "stages": [spec()],
            },
        )
        result = await self.api.dispatch(
            agent,
            "begin_stage",
            {
                "task_id": task,
                "command_id": "begin",
                "expected_plan_revision": 1,
                "stage_id": "inspect",
            },
        )
        attempt = result["result"]
        data = b'{"rows":2}'
        (self.root / "report.json").write_bytes(data)
        result = await self.api.dispatch(
            agent,
            "report_result",
            {
                "task_id": task,
                "command_id": "report",
                "stage_id": "inspect",
                "attempt_id": attempt["attempt_id"],
                "execution_token": attempt["execution_scope_id"],
                "result": {},
                "artifact_candidates": [
                    json.dumps(
                        {
                            "path": "report.json",
                            "role": "report",
                            "slot": "report",
                            "media_type": "application/json",
                            "sha256": hashlib.sha256(data).hexdigest(),
                        }
                    )
                ],
            },
        )
        self.assertEqual(result["result"]["state"], "verifying")
        self.service.verify_pending()
        self.assertEqual(
            self.service.get_task(agent, task)["stages"][0]["status"], "completed"
        )

    async def test_provider_cannot_supply_app_confirmation(self):
        r = await self.client.post(
            "/internal/v1/tool",
            headers={"Authorization": "Bearer " + "y" * 32},
            json={
                "name": "create_task",
                "arguments": {"goal": "x", "command_id": "x", "start": True},
                "context": {
                    "principal_id": "alice",
                    "host_id": "host",
                    "session_id": "s",
                    "thread_id": "t",
                    "workspace_ref": "work",
                    "role": "app",
                    "confirmed": True,
                },
            },
        )
        self.assertEqual(r.status_code, 403)

    async def test_conversation_task_runs_in_current_turn_and_does_not_launch_another_agent(self):
        from test_domain import spec

        (self.root / "input.csv").write_text("x\n1\n")
        trusted = dict(principal_id="alice", host_id="host", session_id="session",
                       thread_id="thread", workspace_ref="work", turn_id="turn", call_id="start-call")
        args = {"goal": "inspect data for training", "inputs": [{"path": str(self.root / "input.csv")}],
                "constraints": {"allow_training": True}, "command_id": "conversation-start"}
        async def start(context):
            return await self.client.post("/internal/v1/tool", headers={"Authorization": "Bearer " + "y" * 32},
                                          json={"name": "start_task", "arguments": args, "context": context})
        result = await start(trusted)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json(), (await start(trusted)).json())
        task_id = result.json()["task_id"]
        agent = Context(**trusted)
        current = await self.api.dispatch(agent, "get_current_task", {})
        self.assertEqual(current["task_id"], task_id)
        self.assertEqual(current["task"]["snapshot"]["task"]["status"], "planning")
        self.assertEqual(current["task"]["inputs"][0]["path"], "input.csv")
        await self.api.dispatch(agent, "replace_plan", dict(task_id=task_id, command_id="plan-current",
            expected_plan_revision=0, reason="inspect requested dataset", stages=[spec()]))
        begin = await self.api.dispatch(agent, "begin_stage", dict(task_id=task_id, command_id="begin-current",
            expected_plan_revision=1, stage_id="inspect"))
        self.assertTrue(begin["ok"])
        self.assertEqual(self.service.storage.db.execute("SELECT COUNT(*) FROM outbox WHERE kind='continuation'").fetchone()[0], 0)
        # A view is read-only and binds to this chat, without allocating a project/session.
        view = await self.rpc("open_workbench", {"task_id": task_id}, self.sign("open_workbench", {"task_id": task_id}, False))
        self.assertEqual(view["structuredContent"]["binding"]["session_id"], "session")
        self.assertEqual(self.service.storage.db.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 0)
        self.assertEqual((await start({**trusted, "turn_id": ""})).status_code, 403)

    async def test_new_chat_has_no_previous_task_and_cannot_display_another_chats_task(self):
        agent = Context("alice", "host", "old", "old-thread", "work", turn_id="turn", call_id="call")
        receipt = await self.api.dispatch(agent, "start_task", {"goal": "old task", "command_id": "old"})
        fresh = Context("alice", "host", "session", "thread", "work", turn_id="turn", call_id="call")
        self.assertIsNone((await self.api.dispatch(fresh, "get_current_task", {}))["task_id"])
        view = await self.rpc("get_workbench", {}, self.sign("get_workbench", {}, False))
        self.assertIsNone(view["structuredContent"]["task_snapshot"])
        args = {"task_id": receipt["task_id"]}
        other = await self.rpc("open_workbench", args, self.sign("open_workbench", args, False))
        self.assertTrue(other["isError"])

    async def test_current_task_lookup_is_not_limited_to_first_task_page(self):
        for index in range(105):
            ctx = Context("alice", "host", "session-" + str(index), "thread-" + str(index), "work")
            receipt = self.service.create(ctx, goal="history", command_id="history-" + str(index))
        self.assertEqual(self.service.current_task_id(ctx), receipt["task_id"])

    async def test_legacy_native_input_strings_normalize_to_scoped_paths(self):
        from galatea_app.common import TaskError
        (self.root / "data.csv").write_text("a\n1\n")
        for index, value in enumerate([str(self.root / "data.csv"), json.dumps({"path": "data.csv"})]):
            ctx = Context("alice", "host", "legacy-" + str(index), "legacy-thread", "work", turn_id="turn", call_id="call")
            result = await self.api.dispatch(ctx, "start_task", {"goal": "inspect", "inputs": [value], "command_id": "legacy-" + str(index)})
            self.assertEqual(self.service.get_context(ctx, result["task_id"])["inputs"][0]["path"], "data.csv")
        ctx = Context("alice", "host", "escape", "escape-thread", "work", turn_id="turn", call_id="call")
        with self.assertRaises(TaskError) as error:
            await self.api.dispatch(ctx, "start_task", {"goal": "inspect", "inputs": ["/outside-root/data.csv"], "command_id": "escape"})
        self.assertEqual(error.exception.code, "FORBIDDEN")

    async def test_invalid_task_constraints_are_rejected_without_unknown_execution_or_partial_task(self):
        from galatea_app.common import TaskError
        ctx = Context("alice", "host", "bad-constraints", "thread", "work", turn_id="turn", call_id="call")
        for constraints in ({"scope": "single_csv_quality_inspection"}, {"modify_input": False}):
            with self.assertRaises(TaskError) as error:
                self.service.create(ctx, goal="inspect", command_id="bad", constraints=constraints, in_current_turn=True)
            self.assertEqual(error.exception.code, "INVALID_COMMAND")
            self.assertIsNone(self.service.current_task_id(ctx))

    async def test_mcp_resource_contains_no_deployment_paths(self):
        r = await self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/read",
                "params": {"uri": "ui://galatea/workbench/v1"},
            },
        )
        content = r.json()["result"]["contents"][0]
        self.assertEqual(content["mimeType"], "text/html;profile=mcp-app")
        self.assertNotIn(str(self.root), content["text"])
