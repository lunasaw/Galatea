"""Real Host/AppBridge/signature/SQLite with synthetic native responses; no training."""

import asyncio
from pathlib import Path
import sys
import tempfile
import secrets
import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PrivateFormat,
    NoEncryption,
)
from console_browser_fixture import BrowserHost, FixtureNative
from codex_agent.console.server import ConsoleApp
from codex_agent.console.service import ConsoleObservationService
from codex_agent.app_integrations import AppIntegrations
from galatea_app.api import AppAPI
from galatea_app.service import TaskService, Context
from galatea_app.trust import ProofVerifier
from galatea_app.projects import Projects
from galatea_app.workers import Worker, HostClient
from galatea_app.contracts import APP_TOOLS, READ_ONLY


class Native(FixtureNative):
    async def call(self, method, params, request_id):
        if method == "mcpServerStatus/list":
            tools = (await self.host.app.rpc({"method": "tools/list"}))["tools"]
            tools.append(
                {
                    "name": "lookup",
                    "inputSchema": {"type": "object"},
                    "annotations": {"readOnlyHint": True},
                    "_meta": {"ui": {"resourceUri": "ui://fixture/app"}},
                }
            )
            return {
                "data": [
                    {
                        "name": "fixture-app",
                        "authStatus": "unsupported",
                        "tools": {t["name"]: t for t in tools},
                        "resources": [],
                        "resourceTemplates": [],
                    }
                ],
                "nextCursor": None,
            }
        if method == "mcpServer/resource/read":
            result = await self.host.app.rpc(
                {
                    "method": "resources/read",
                    "params": {"uri": "ui://galatea/workbench/v1"},
                }
            )
            result["contents"][0]["uri"] = params["uri"]
            return result
        if method == "mcpServer/tool/call":
            return await self.host.app.rpc(
                {
                    "method": "tools/call",
                    "params": {
                        "name": params["tool"],
                        "arguments": params.get("arguments", {}),
                        "_meta": params.get("_meta", {}),
                    },
                }
            )
        return await super().call(method, params, request_id)


class Host(BrowserHost):
    async def create_session(self, session_id=None, **kwargs):
        kwargs.setdefault("options", {}).setdefault("cwd", str(self.workspace_dir))
        return await super().create_session(session_id, **kwargs)

    async def send_message(self, session_id, request_id, text, **kwargs):
        if text != "conversation-task-fixture":
            return await super().send_message(session_id, request_id, text, **kwargs)
        from test_domain import spec
        turn = "turn-" + request_id
        self.sessions[session_id].update(status="running", active_turn_id=turn)
        self.events.bind(session_id, session_id)
        self.events.append(session_id, {"kind": "message_accepted", "thread_id": session_id,
            "turn_id": turn, "host_request_id": request_id, "public": {"text": text}})
        def wire(method, params):
            self.events.observer("runtime_to_host", {"method": method, "params": {
                "threadId": session_id, "turnId": turn, **params}}, runtime_instance_id="browser-fixture")
        ctx = Context("p", "host", session_id, session_id, "work", turn_id=turn, call_id=request_id)
        wire("turn/started", {"turn": {"id": turn, "status": "inProgress"}})
        task_id = (await self.app.dispatch(ctx, "start_task", {
            "goal": "检查这份 CSV 并交付质量报告", "inputs": [{"path": "input/data.csv"}],
            "command_id": request_id + "-start"}))["task_id"]
        import json
        wire("item/completed", {"item": {"id": "progress-app", "type": "dynamicToolCall",
            "namespace": "galatea_task", "tool": "start_task", "status": "completed", "success": True,
            "arguments": {"goal": "检查 CSV", "command_id": request_id + "-start"},
            "contentItems": [{"type": "inputText", "text": json.dumps({
                "schema_version": "codex-agent.tools/v1", "ok": True, "data": {
                    "task_id": task_id, "app_view": {"server": "fixture-app",
                        "resource_uri": "ui://galatea/workbench/v1", "arguments": {"task_id": task_id}}}})}]}})
        await asyncio.sleep(3)
        await self.app.dispatch(ctx, "replace_plan", {"task_id": task_id, "command_id": request_id + "-plan",
            "expected_plan_revision": 0, "reason": "inspect fixture", "stages": [spec()]})
        attempt = (await self.app.dispatch(ctx, "begin_stage", {"task_id": task_id,
            "command_id": request_id + "-begin", "expected_plan_revision": 1, "stage_id": "inspect"}))["result"]
        await asyncio.sleep(3)
        (self.workspace_dir / "quality.json").write_text('{"rows": 2, "missing": 0}')
        await self.app.dispatch(ctx, "report_result", {"task_id": task_id, "command_id": request_id + "-report",
            "stage_id": "inspect", "attempt_id": attempt["attempt_id"], "execution_token": attempt["execution_scope_id"],
            "result": {"rows": 2}, "artifact_candidates": [
                {"path": "quality.json", "role": "report", "slot": "report", "media_type": "application/json"}]})
        self.app.service.verify_pending()
        snapshot = self.app.service.get_task(ctx, task_id)
        await self.app.dispatch(ctx, "complete_task", {"task_id": task_id, "command_id": request_id + "-complete",
            "expected_plan_revision": 1, "summary": "已核验两行数据并生成质量报告。",
            "artifact_refs": [a["id"] for a in snapshot["artifacts"]]})
        wire("item/completed", {"item": {"id": "answer", "type": "agentMessage",
            "phase": "final_answer", "text": "质量报告已生成，可在右侧查看。"}})
        wire("turn/completed", {"turn": {"id": turn, "status": "completed"}})
        self.sessions[session_id].update(status="ready", active_turn_id=None)
        return {"request_id": request_id, "turn_id": turn, "status": "accepted"}


class Combined:
    def __init__(self, console, worker):
        self.console, self.worker = console, worker

    async def __call__(self, scope, receive, send):
        if scope["type"] != "lifespan":
            return await self.console(scope, receive, send)
        task = None

        async def wrap(message):
            nonlocal task
            if message["type"] == "lifespan.startup.complete":
                task = asyncio.create_task(self.worker.run())
            if message["type"] == "lifespan.shutdown.complete" and task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await send(message)

        await self.console(scope, receive, wrap)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="galatea-signed-browser-") as d:
        root = Path(d)
        (root / "input").mkdir()
        (root / "input/data.csv").write_text("x\n1\n2\n")
        key = Ed25519PrivateKey.generate()
        private = root / "private.pem"
        private.write_bytes(
            key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        )
        private.chmod(0o600)
        token = secrets.token_hex(32)
        credential = root / "service.token"
        credential.write_text(token)
        credential.chmod(0o600)
        port = int(sys.argv[1])
        events = ConsoleObservationService()
        host = Host(events, root)
        host.native = Native(host)
        service = TaskService(root / "tasks.sqlite3", workspaces={"work": root})
        client = HostClient(f"http://127.0.0.1:{port}", token)
        projects = Projects(service, client, workspace_ref="work")
        worker = Worker(service, client, projects=projects, interval=0.15)
        host.app = AppAPI(
            service,
            verifier=ProofVerifier(
                {
                    "key": {
                        "public_key": key.public_key(),
                        "host_id": "host",
                        "principal_id": "p",
                    }
                },
                "app",
            ),
            credentials={},
            projects=projects,
            example={"path": "input", "goal": "检查并清理这份数据，只做两个必要阶段"},
        )
        integration = AppIntegrations(
            host,
            events,
            {
                "enabled": True,
                "host_id": "host",
                "key_id": "key",
                "private_key_file": str(private),
                "service_credentials": {"app": str(credential)},
                "workspaces": {"work": str(root)},
                "servers": {
                    "fixture-app": {
                        "audience": "app",
                        "resources": ["ui://fixture/app", "ui://galatea/workbench/v1"],
                        "tools": {n: {"read_only": n in READ_ONLY} for n in APP_TOOLS},
                    }
                },
            },
        )
        uvicorn.run(
            Combined(ConsoleApp(host, events, integrations=integration), worker),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
