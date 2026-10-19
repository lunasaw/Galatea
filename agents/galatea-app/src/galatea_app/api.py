"""Authenticated internal API and stateless MCP Streamable HTTP endpoint."""

from __future__ import annotations
import asyncio
from dataclasses import asdict, replace
import json
from pathlib import Path
import secrets
from urllib.parse import parse_qs

from .common import TaskError, canonical, require, uid
from .contracts import (
    SCHEMAS,
    APP_TOOLS,
    AGENT_TOOLS,
    READ_ONLY,
    DESCRIPTIONS,
    validate,
)
from .projections import Workbench
from .service import Context

RESOURCE = "ui://galatea/workbench/v1"
MIME = "text/html;profile=mcp-app"


class AppAPI:
    def __init__(
        self,
        service,
        *,
        verifier,
        credentials,
        worker=None,
        example=None,
        projects=None,
    ):
        self.service, self.verifier, self.credentials, self.worker = (
            service,
            verifier,
            credentials,
            worker,
        )
        self.workbench = Workbench(service)
        self.example = example
        self.projects = projects
        self.task = None

    async def dispatch(self, ctx, name, args):
        # Older native schema clients encode unconstrained array objects as JSON strings.
        if name in {"start_task", "create_task"} and isinstance(args.get("inputs"), list):
            args = {**args, "inputs": [
                (json.loads(v) if v.lstrip().startswith("{") else {"path": v})
                if isinstance(v, str) else v for v in args["inputs"]]}
        if name == "replace_plan" and isinstance(args.get("stages"), list):
            args = {
                **args,
                "stages": [
                    json.loads(v) if isinstance(v, str) else v for v in args["stages"]
                ],
            }
        if name == "report_result" and isinstance(
            args.get("artifact_candidates"), list
        ):
            args = {
                **args,
                "artifact_candidates": [
                    json.loads(v) if isinstance(v, str) else v
                    for v in args["artifact_candidates"]
                ],
            }
        validate(name, args)
        if name == "create_task":
            return await asyncio.to_thread(self.service.create, ctx, **args)
        if name == "start_task":
            return await asyncio.to_thread(self.service.create, ctx, in_current_turn=True, **args)
        if name == "get_current_task":
            task_id = self.service.current_task_id(ctx)
            if task_id:
                with self.service.storage.lock:
                    row = self.service.storage.db.execute(
                        "SELECT aggregate_json FROM tasks WHERE id=?", (task_id,)
                    ).fetchone()
                    ctx = replace(ctx, workspace_ref=json.loads(row[0])["binding"]["workspace_ref"])
            return {"task_id": task_id, "task": self.service.get_context(ctx, task_id) if task_id else None}
        if (
            name
            in {
                "launch_project",
                "activate_project_task",
                "get_activation",
                "list_projects",
            }
            and self.projects
        ):
            return await self.projects.call(ctx, name, args)
        if name == "get_task":
            value = await asyncio.to_thread(
                (
                    self.service.get_context
                    if ctx.role == "agent"
                    else self.service.get_task
                ),
                ctx,
                args["task_id"],
            )
            return value
        if name == "list_tasks":
            return self.service.list_tasks(ctx, **args)
        if name == "list_projects":
            return {
                "items": [
                    {
                        "project_id": t["workspace_ref"],
                        "name": t["title"],
                        "slug": t["workspace_ref"],
                        "task_sessions": [t],
                    }
                    for t in self.service.list_tasks(ctx, **args)["items"]
                ]
            }
        if name in {"get_workbench", "open_workbench"}:
            task_id = args.get("task_id")
            if not task_id:
                task_id = self.service.current_task_id(ctx)
            if task_id:
                with self.service.storage.lock:
                    binding = self.service._load(self.service.storage.db, ctx, task_id)["binding"]
                    require(all(binding[k] == getattr(ctx, k) for k in ("host_id", "session_id", "thread_id")), "FORBIDDEN")
            return (
                self.workbench.view(ctx, task_id)
                if task_id
                else {
                    "schema_version": "galatea-workbench-view/v1",
                    "task_snapshot": None,
                    "capabilities": {"presentation": True},
                }
            )
        if name == "get_task_events":
            return self.service.events(ctx, **args)
        if name == "get_workbench_events":
            return self.workbench.events(ctx, **args)
        if name == "get_stage_details":
            return self.workbench.stage_details(ctx, **args)
        if name == "get_artifact":
            return self.service.artifact(ctx, args["task_id"], args["artifact_id"])
        if name == "get_file_change":
            return self.workbench.file_change(ctx, **args)
        if name == "get_command_receipt":
            return self.service.get_receipt(ctx, **args)
        if name == "get_training_proposal":
            context = self.service.get_context(ctx, args["task_id"])
            proposals = [
                v
                for v in context["proposals"]
                if v["proposal_id"] == args["proposal_id"]
            ]
            require(proposals, "NOT_FOUND")
            return {
                "proposal": proposals[0],
                "requests": [
                    r
                    for r in context["pending_requests"]
                    if r["proposal_id"] == args["proposal_id"]
                ],
            }
        payload = dict(args)
        task_id = payload.pop("task_id")
        command_id = payload.pop("command_id")
        pre = {
            k: payload.pop(k)
            for k in ("expected_version", "expected_plan_revision")
            if k in payload
        }
        return await asyncio.to_thread(
            self.service.command,
            ctx,
            task_id,
            name,
            payload,
            command_id=command_id,
            **pre,
        )

    def authenticate(self, headers, role=None):
        value = headers.get("authorization", "").removeprefix("Bearer ")
        for id, entry in self.credentials.items():
            if secrets.compare_digest(value, entry["token"]) and (
                role is None or entry["role"] == role
            ):
                return id, entry
        raise TaskError("UNAUTHENTICATED")

    async def rpc(self, request):
        method = request.get("method")
        params = request.get("params", {})
        if method == "initialize":
            return {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "galatea-app", "version": "0.1.0"},
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": name,
                        "description": DESCRIPTIONS.get(name, name.replace("_", " ")),
                        "inputSchema": SCHEMAS[name],
                        "annotations": {
                            "readOnlyHint": name in READ_ONLY,
                            "idempotentHint": True,
                        },
                        "_meta": {
                            "ui": {
                                "resourceUri": RESOURCE,
                                **(
                                    {"visibility": ["app"]}
                                    if name != "open_workbench"
                                    else {}
                                ),
                            },
                            "io.codex.host/require-call-proof": True,
                        },
                    }
                    for name in sorted(APP_TOOLS)
                ]
            }
        if method == "resources/list":
            return {
                "resources": [
                    {"uri": RESOURCE, "name": "Galatea 工作台", "mimeType": MIME}
                ]
            }
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
        if method == "resources/read":
            require(params.get("uri") == RESOURCE, "NOT_FOUND")
            return {
                "contents": [
                    {
                        "uri": RESOURCE,
                        "mimeType": MIME,
                        "text": (
                            Path(__file__).parent / "static/workbench.html"
                        ).read_text(),
                    }
                ]
            }
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            require(name in APP_TOOLS, "FORBIDDEN")
            proof = params.get("_meta", {}).get("io.codex.host/call-proof")
            if name == "open_workbench" and proof is None:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "已请求在当前聊天展示任务进度与结果。视图通过 Host 读取本会话任务；继续在当前聊天执行和沟通。",
                        }
                    ],
                    "structuredContent": {
                        "resource_uri": RESOURCE,
                        "requires_host_proof": True,
                    },
                    "_meta": {"ui": {"resourceUri": RESOURCE}},
                }
            try:
                ctx = self.verifier.verify(
                    proof, name, args, write=name not in READ_ONLY
                )
                result = await self.dispatch(ctx, name, args)
                return {
                    "content": [{"type": "text", "text": canonical(result)}],
                    "structuredContent": result,
                }
            except TaskError as exc:
                return {
                    "isError": True,
                    "content": [{"type": "text", "text": str(exc)}],
                    "structuredContent": exc.envelope(),
                }
        raise TaskError("INVALID_COMMAND", "Unsupported MCP method")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup":
                    if self.worker:
                        self.task = asyncio.create_task(self.worker.run())
                    await send({"type": "lifespan.startup.complete"})
                else:
                    if self.task:
                        self.task.cancel()
                        await asyncio.gather(self.task, return_exceptions=True)
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return

        async def respond(status, value, content_type="application/json"):
            body = value if isinstance(value, bytes) else canonical(value).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (b"content-type", content_type.encode()),
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})

        path = scope["path"]
        method = scope["method"]
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        try:
            if path == "/health/live":
                return await respond(200, {"live": True})
            if path == "/health/ready":
                return await respond(
                    200, {"ready": bool(self.credentials and self.verifier.keyset)}
                )
            role, credential = self.authenticate(headers)
            if path == "/health/dependencies":
                return await respond(
                    200,
                    {
                        **self.service.storage.health(),
                        "worker_error": self.worker.last_error if self.worker else None,
                        "worker_last_tick_at": (
                            self.worker.last_tick_at if self.worker else None
                        ),
                        "governance_routes": list(self.service.governance),
                    },
                )
            body = {}
            if method == "POST":
                chunks = []
                size = 0
                while True:
                    event = await asyncio.wait_for(receive(), 15)
                    data = event.get("body", b"")
                    size += len(data)
                    require(size <= 1024 * 1024, "RATE_LIMITED")
                    chunks.append(data)
                    if not event.get("more_body"):
                        break
                body = json.loads(b"".join(chunks) or b"{}")
                require(isinstance(body, dict), "INVALID_COMMAND")
            if path == "/mcp":
                if method == "DELETE":
                    return await respond(200, {})
                if method != "POST":
                    return await respond(405, {"error": "Use stateless MCP POST"})
                if body.get("method", "").startswith("notifications/"):
                    return await respond(202, b"")
                try:
                    result = await self.rpc(body)
                    response = {
                        "jsonrpc": "2.0",
                        "id": body.get("id"),
                        "result": result,
                    }
                except TaskError as exc:
                    response = {
                        "jsonrpc": "2.0",
                        "id": body.get("id"),
                        "error": {"code": -32602, "message": str(exc)},
                    }
                return await respond(200, response)
            require(path.startswith("/internal/v1/"), "NOT_FOUND")
            if path == "/internal/v1/runtime-events":
                require(credential["role"] == "exporter", "FORBIDDEN")
                for event in body["events"]:
                    require(event["host_id"] == credential["host_id"], "FORBIDDEN")
                    self.workbench.ingest(body["task_id"], event)
                return await respond(200, {"accepted": len(body["events"])})
            require(credential["role"] == "provider", "FORBIDDEN")
            trusted = body.get("context", {})
            require(
                trusted.get("principal_id") == credential["principal_id"]
                and trusted.get("host_id") == credential["host_id"],
                "FORBIDDEN",
            )
            require(
                set(trusted) <= {k for k in Context.__dataclass_fields__}
                and not trusted.get("confirmed")
                and trusted.get("role", "agent") == "agent",
                "FORBIDDEN",
            )
            if body.get("arguments", {}).get("task_id"):
                # Session binding comes from the service aggregate and authenticated Provider context,
                # never the model's task arguments or a static workspace assumption after activation.
                with self.service.storage.lock:
                    row = self.service.storage.db.execute(
                        "SELECT aggregate_json FROM tasks WHERE id=? AND owner_id=?",
                        (body["arguments"]["task_id"], trusted["principal_id"]),
                    ).fetchone()
                    require(row is not None, "NOT_FOUND")
                    aggregate = json.loads(row[0])
                    binding = aggregate["binding"]
                    require(
                        all(
                            binding[k] == trusted.get(k)
                            for k in ("host_id", "session_id", "thread_id")
                        ),
                        "FORBIDDEN",
                    )
                    trusted["workspace_ref"] = binding["workspace_ref"]
            ctx = Context(**trusted)
            if path == "/internal/v1/tool":
                require(body["name"] in AGENT_TOOLS, "FORBIDDEN")
                result = await self.dispatch(
                    ctx, body["name"], body.get("arguments", {})
                )
            elif path == "/internal/v1/receipt":
                result = self.service.get_receipt(ctx, body["command_id"])
            else:
                raise TaskError("NOT_FOUND")
            return await respond(200, result)
        except TaskError as exc:
            status = {
                "UNAUTHENTICATED": 401,
                "FORBIDDEN": 403,
                "AUTHORIZATION_REQUIRED": 403,
                "NOT_FOUND": 404,
                "RATE_LIMITED": 429,
                "UNAVAILABLE": 503,
            }.get(
                exc.code,
                (
                    409
                    if exc.code.endswith("CONFLICT")
                    or exc.code in {"EXECUTION_UNKNOWN", "STALE_EXECUTION"}
                    else 422
                ),
            )
            return await respond(status, exc.envelope())
        except (ValueError, KeyError, TypeError):
            return await respond(
                422,
                {"ok": False, "code": "INVALID_COMMAND", "message": "Invalid request"},
            )
