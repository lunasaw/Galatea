"""Real Codex 0.163.0 + controlled Responses model + real task SQLite/artifacts.

The model is synthetic; this validates the native protocol, not model intelligence.
"""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT.parent / "codex-app-server"
sys.path.insert(0, str(HOST / "scripts"))
from runtime_probe import mock_config, sse
from codex_agent.catalog import Catalog
from codex_agent.config import AgentConfig
from codex_agent.events import EventLog
from codex_agent.host import AgentHost
from codex_agent.process import ManagedProcess
from codex_agent.registry import ToolRegistry, ToolResult
from codex_agent.state import AtomicJsonStore, OperationJournal
from galatea_app.service import Context, TaskService
from test_domain import spec


@unittest.skipUnless(
    os.environ.get("CODEX_TEST_RUNTIME_PACKAGE"), "real Codex package required"
)
class NativeTaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_native_dynamic_tools_deliver_two_stages(self):
        requests = []
        state = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(body)
                index = len(requests)
                task = state["task"]
                service = state["service"]
                ctx = state["ctx"]
                calls = [
                    (
                        "replace_plan",
                        {
                            "task_id": task,
                            "command_id": "plan",
                            "expected_plan_revision": 0,
                            "reason": "two required data steps",
                            "stages": [spec(), spec("clean", ["inspect"])],
                        },
                    ),
                    (
                        "begin_stage",
                        {
                            "task_id": task,
                            "command_id": "begin1",
                            "expected_plan_revision": 1,
                            "stage_id": "inspect",
                        },
                    ),
                ]
                if index in (3, 5):
                    a = state["attempt"]
                    stage = "inspect" if index == 3 else "clean"
                    # A component fixture supplies the actual file; Task verifies real bytes.
                    (state["root"] / (stage + ".json")).write_text(
                        json.dumps({"rows": 2 if stage == "inspect" else 1})
                    )
                    name = "report_result"
                    args = {
                        "task_id": task,
                        "command_id": "result-" + stage,
                        "stage_id": stage,
                        "attempt_id": a["attempt_id"],
                        "execution_token": a["execution_scope_id"],
                        "result": {"rows": 2},
                        "artifact_candidates": [
                            {
                                "path": stage + ".json",
                                "role": "report",
                                "slot": "report",
                                "media_type": "application/json",
                            }
                        ],
                    }
                elif index == 4:
                    service.verify_pending()
                    name = "begin_stage"
                    args = {
                        "task_id": task,
                        "command_id": "begin2",
                        "expected_plan_revision": 1,
                        "stage_id": "clean",
                    }
                elif index == 6:
                    service.verify_pending()
                    name = "complete_task"
                    args = {
                        "task_id": task,
                        "command_id": "complete",
                        "expected_plan_revision": 1,
                        "summary": "Actual artifact fixture delivered",
                        "artifact_refs": [
                            a["id"] for a in service.get_task(ctx, task)["artifacts"]
                        ],
                    }
                elif index <= 2:
                    name, args = calls[index - 1]
                else:
                    name = None
                items = (
                    [
                        {
                            "type": "function_call",
                            "call_id": "call-" + str(index),
                            "namespace": "galatea_task",
                            "name": name,
                            "arguments": json.dumps(args),
                        }
                    ]
                    if name
                    else [
                        {
                            "id": "final",
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Verified task complete.",
                                }
                            ],
                        }
                    ]
                )
                payload = sse(items, "native-task-" + str(index))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workspace = root / "workspace"
                workspace.mkdir()
                (root / "codex-home").mkdir()
                (root / "codex-home/config.toml").write_text(
                    mock_config(server.server_port)
                )
                package = Path(os.environ["CODEX_TEST_RUNTIME_PACKAGE"])
                config = AgentConfig.from_dict(
                    {
                        "schema_version": "codex-agent/v2",
                        "paths": {
                            "codex_home": "codex-home",
                            "runtime_dir": str(package),
                            "state_dir": "state",
                            "workspace_dir": "workspace",
                        },
                        "codex": {
                            "binary": str(package / "bin/codex"),
                            "runtime_version": "codex-cli 0.163.0",
                        },
                        "agent": {"id": "alice"},
                    },
                    root,
                )
                store = AtomicJsonStore(config.state_dir)
                events = EventLog()
                catalog = Catalog.load(ROOT / "contracts")
                service = TaskService(root / "tasks.db", workspaces={"work": workspace})

                def handler(name):
                    def call(context, args):
                        ctx = Context(
                            context.principal_id,
                            "host",
                            context.session_id,
                            context.thread_id,
                            "work",
                            turn_id=context.turn_id,
                            call_id=context.call_id,
                        )
                        payload = dict(args)
                        task = payload.pop("task_id")
                        key = payload.pop("command_id")
                        pre = {
                            k: payload.pop(k)
                            for k in ("expected_plan_revision", "expected_version")
                            if k in payload
                        }
                        result = service.command(
                            ctx, task, name, payload, command_id=key, **pre
                        )
                        if name == "begin_stage":
                            state["attempt"] = result["result"]
                        return result

                    return call

                registry = ToolRegistry(
                    catalog,
                    OperationJournal(store),
                    {name: handler(name) for name in catalog.schemas},
                )
                process = ManagedProcess(
                    config, observer=events.observer, diagnostic=events.diagnostic
                )

                async def start():
                    return await process.start(
                        host.handle_tool_call,
                        notification=host.handle_notification,
                        on_failure=host.runtime_failed,
                    )

                host = AgentHost(
                    registry=registry,
                    event_log=events,
                    store=store,
                    client_factory=start,
                    client_stop=process.stop,
                    reconciler=lambda: None,
                    principal_id="alice",
                    actions={
                        "replace_plan",
                        "begin_stage",
                        "report_result",
                        "complete_task",
                    },
                    workspace_dir=workspace,
                )
                try:
                    await host.start()
                    session = await host.create_session("native-task")
                    ctx = Context(
                        "alice",
                        "host",
                        session["id"],
                        session["thread_id"],
                        "work",
                        role="app",
                        confirmed=True,
                        interaction_id="fixture-confirm",
                    )
                    task = service.create(
                        ctx,
                        command_id="create",
                        goal="clean two row fixture",
                        start=True,
                    )["task_id"]
                    state.update(task=task, service=service, ctx=ctx, root=workspace)
                    await host.send_message(
                        session["id"],
                        "native-request",
                        "Execute the bounded synthetic task protocol fixture",
                    )
                    async with asyncio.timeout(30):
                        while host.get_session(session["id"])["status"] != "ready":
                            await asyncio.sleep(0.02)
                    snapshot = service.get_task(ctx, task)
                    self.assertEqual(snapshot["task"]["status"], "completed")
                    self.assertEqual(len(snapshot["stages"]), 2)
                    self.assertEqual(len(requests), 7)
                    self.assertEqual(host.get_session(session["id"])["last_turn_status"], "completed")
                    self.assertNotIn('"name": "get_goal"', json.dumps(requests[0]["tools"]))
                    self.assertTrue(
                        all(
                            e["verification"] == "verified"
                            for e in snapshot["evidence"]
                        )
                    )
                    self.assertEqual(
                        store.read("requests/native-request.json")["status"],
                        "completed",
                    )
                    self.assertIn(
                        "execution_scope_id", json.dumps(requests[2]["input"])
                    )
                finally:
                    await host.stop()
                    await events.close()
                    service.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
