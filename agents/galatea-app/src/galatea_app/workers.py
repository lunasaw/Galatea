"""Deterministic observation and delivery. Never creates a model client."""

import asyncio
import json
import time
from .common import TaskError, digest, utc
from .projections import Workbench
from .service import Context


class HostClient:
    def __init__(self, url, token):
        import httpx

        self.client = httpx.AsyncClient(
            base_url=url, headers={"Authorization": "Bearer " + token}, timeout=15
        )

    async def request(self, method, path, **kwargs):
        r = await self.client.request(method, path, **kwargs)
        r.raise_for_status()
        return r.json()

    async def activate(self, payload):
        return await self.request("POST", "/internal/v1/app-activations", json=payload)

    async def close(self):
        await self.client.aclose()


class Worker:
    def __init__(self, service, host, *, interval=5, projects=None):
        self.service, self.host, self.interval, self.projects = (
            service,
            host,
            interval,
            projects,
        )
        self.workbench = Workbench(service)
        self.last_error = None
        self.last_tick_at = None

    async def run(self):
        while True:
            try:
                await self.tick()
                self.last_tick_at = utc()
            except Exception as exc:
                self.last_error = type(exc).__name__
            await asyncio.sleep(self.interval)

    async def tick(self):
        self.last_error = None
        if self.service.storage.health()["recovery_mode"]:
            return
        if self.projects:
            await self.projects.tick()
        await asyncio.to_thread(self.service.enforce_deadlines)
        await asyncio.to_thread(self.service.verify_pending)
        with self.service.storage.lock:
            aggregates = [
                json.loads(r[0])
                for r in self.service.storage.db.execute(
                    "SELECT aggregate_json FROM tasks"
                ).fetchall()
            ]
        for a in aggregates:
            t = a["snapshot"]["task"]
            if t["status"] in {"waiting_approval", "waiting_input"}:
                try:
                    await self.approve_training(t["id"], a["binding"], a["owner_id"])
                except Exception as exc:
                    self.last_error = "automatic_training_approval:" + type(exc).__name__
            reconciled_intents = set()
            for intent in a["intents"].values():
                if intent["state"] not in {"prepared", "unknown"}:
                    continue
                adapter = self.service.governance.get(intent["route"])
                if not hasattr(adapter, "reconcile"):
                    continue
                try:
                    operation = await asyncio.to_thread(adapter.reconcile, intent)
                except Exception as exc:
                    self.last_error = "reconcile_external:" + type(exc).__name__
                    continue
                if operation:
                    self.service.reconcile_external(t["id"], intent["id"], operation)
                else:
                    reconciled_intents.add(intent["id"])
            for operation in a["snapshot"]["external_operations"]:
                if operation["status"] in {"succeeded", "failed", "cancelled"}:
                    continue
                adapter = self.service.governance.get(operation["provider"])
                if adapter is None:
                    continue
                try:
                    fact = await asyncio.to_thread(adapter.observe, operation)
                except Exception as exc:
                    self.last_error = "observe_external:" + type(exc).__name__
                    fact = {"status": "unknown"}
                # Poll timestamps are not business facts. Include the revision
                # so a later A -> B -> A transition is not mistaken for replay.
                fact = {k: v for k, v in fact.items() if k != "observed_at"}
                self.service.observe(
                    t["id"],
                    operation["id"],
                    fact,
                    source=operation["provider"],
                    message_id=digest([operation["id"], fact, t["version"]]),
                )
            if a.get("host_continuation_binding"):
                try:
                    cursor = a.get("export_cursor")
                    query = {"binding_id": a["host_continuation_binding"]}
                    if cursor:
                        query["after"] = cursor
                    batch = await self.host.request(
                        "GET", "/internal/v1/runtime-events", params=query
                    )
                    for event in batch["events"]:
                        self.workbench.ingest(t["id"], event)
                    with self.service.storage.transaction() as db:
                        current = json.loads(
                            db.execute(
                                "SELECT aggregate_json FROM tasks WHERE id=?",
                                (t["id"],),
                            ).fetchone()[0]
                        )
                        current["export_cursor"] = batch["next_cursor"]
                        self.service._save(db, current, increment=False)
                    runtime = await self.host.request(
                        "GET", "/internal/v1/continuation-bindings/" + a["host_continuation_binding"]
                    )
                    self.service.reconcile_idle_task(
                        t["id"], runtime, reconciled_intents=reconciled_intents
                    )
                except Exception as exc:
                    self.last_error = "host_reconciliation:" + type(exc).__name__
        for _ in range(20):
            job = self.service.storage.claim("worker")
            if not job:
                break
            try:
                await self.deliver(job)
                self.service.storage.ack(job["id"], job["fencing_token"])
            except TaskError as exc:
                if exc.code != "STALE_EXECUTION":
                    self.service.storage.ack(
                        job["id"], job["fencing_token"], error=exc.code
                    )
            except Exception as exc:
                self.service.storage.ack(
                    job["id"], job["fencing_token"], error=type(exc).__name__
                )

    async def approve_training(self, task_id, binding, principal_id):
        """Request exact decisions; only the Host can approve them automatically."""
        ctx = Context(principal_id=principal_id, **{k: binding[k] for k in (
            "host_id", "session_id", "thread_id", "workspace_ref"
        )})
        context = await asyncio.to_thread(self.service.get_context, ctx, task_id)
        for request in context["pending_requests"]:
            if request["status"] != "open" or request["kind"] != "training_proposal":
                continue
            # Authorization changes the version and availability. Reload before
            # confirmation and never resume a deferred or explicitly paused task.
            for _ in range(2):
                current = await asyncio.to_thread(self.service.get_context, ctx, task_id)
                task = current["snapshot"]["task"]
                if task["status"] not in {"waiting_approval", "waiting_input"}:
                    break
                latest_request = next(r for r in current["pending_requests"]
                                      if r["request_id"] == request["request_id"])
                if latest_request["status"] != "open":
                    break
                availability = current["training_decisions"][request["proposal_id"]]
                decision = ("authorize" if availability["can_authorize"] else
                            "confirm" if availability["can_confirm"] else None)
                if not decision:
                    break
                result = await self.host.request(
                    "POST", "/internal/v1/app-tool-calls",
                    json={
                        "host_session_id": ctx.session_id,
                        "server": "galatea_app",
                        "uri": "ui://galatea/workbench/v1",
                        "name": "submit_input",
                        "arguments": {
                            "task_id": task_id,
                            "command_id": "auto-training-" + digest([
                                task_id, request["request_id"], decision, task["version"]
                            ]),
                            "expected_version": task["version"],
                            "request_id": request["request_id"],
                            "values": {"proposal_digest": request["subject_digest"],
                                       "decision": decision},
                        },
                    },
                )
                if result.get("state") != "completed":
                    return
                if result.get("result", {}).get("isError"):
                    raise TaskError("AUTHORIZATION_REQUIRED", "Automatic decision rejected")

    async def deliver(self, job):
        with self.service.storage.lock:
            a = json.loads(
                self.service.storage.db.execute(
                    "SELECT aggregate_json FROM tasks WHERE id=?", (job["task_id"],)
                ).fetchone()[0]
            )
        t = a["snapshot"]["task"]
        payload = job["payload"]
        kind = job["kind"]
        if kind == "verify":
            return
        if kind == "revoke_continuation":
            await self.host.request(
                "POST",
                "/internal/v1/continuation-bindings/" + payload["binding_id"],
                json={},
            )
            return
        if kind == "continuation":
            if (
                t["status"]
                in {"completed", "failed", "cancelled", "paused", "cancelling"}
                or t["control_epoch"] != payload["control_epoch"]
            ):
                return
            binding = a.get("host_continuation_binding")
            if not binding:
                result = await self.host.request(
                    "POST",
                    "/internal/v1/continuation-bindings",
                    json={
                        "request_id": t["binding_id"] + "-" + str(t["control_epoch"]),
                        "host_session_id": a["binding"]["session_id"],
                        "launch_receipt": a["launch_receipt"],
                        "source_ref": t["id"],
                        "expires_at": t["constraints"]["deadline_at"],
                        "max_continuations": t["constraints"]["max_auto_continuations"],
                    },
                )
                binding = result["binding_id"]
                with self.service.storage.transaction() as db:
                    current = json.loads(
                        db.execute(
                            "SELECT aggregate_json FROM tasks WHERE id=?", (t["id"],)
                        ).fetchone()[0]
                    )
                    current["host_continuation_binding"] = binding
                    self.service._save(db, current, increment=False)
            result = await self.host.request(
                "POST",
                "/internal/v1/continuations",
                json={
                    "schema_version": "codex-continuation/v1",
                    "request_id": job["id"],
                    "host_session_id": a["binding"]["session_id"],
                    "binding_id": binding,
                    "not_before": t["created_at"],
                    "expires_at": t["constraints"]["deadline_at"],
                    "context": {
                        "kind": "untrusted",
                        "source": "galatea-task-observer",
                        "task_id": t["id"],
                        "event_seq": payload["event_seq"],
                        "summary": "任务事实发生变化。加载 galatea-task-workflow Skill，调用 galatea_task.get_task 核对事实后继续。原生 Goal 与此 Task 无关，不要轮询 get_goal。",
                        "reason": payload["reason"],
                    },
                },
            )
            with self.service.storage.transaction() as db:
                current = json.loads(
                    db.execute(
                        "SELECT aggregate_json FROM tasks WHERE id=?", (t["id"],)
                    ).fetchone()[0]
                )
                self.service._event(
                    db,
                    current,
                    "continuation.accepted",
                    {"request_id": job["id"], "state": result["state"]},
                    job["id"],
                )
                self.service._save(db, current, increment=False)
        elif kind in {"control", "stop_external"}:
            if payload.get("control_epoch", t["control_epoch"]) != t["control_epoch"]:
                return
            binding = a.get("host_continuation_binding")
            if not binding:
                result = await self.host.request(
                    "POST",
                    "/internal/v1/continuation-bindings",
                    json={
                        "request_id": t["binding_id"] + "-" + str(t["control_epoch"]),
                        "host_session_id": a["binding"]["session_id"],
                        "launch_receipt": a["launch_receipt"],
                        "source_ref": t["id"],
                        "expires_at": t["constraints"]["deadline_at"],
                        "max_continuations": max(
                            1, t["constraints"]["max_auto_continuations"]
                        ),
                    },
                )
                binding = result["binding_id"]
            if binding:
                await self.host.request(
                    "POST", "/internal/v1/continuation-bindings/" + binding, json={}
                )
            if kind == "control" and payload["action"] in {"interrupt_agent", "cancel"}:
                await self.host.request(
                    "POST",
                    "/internal/v1/interrupt",
                    json={
                        "binding_id": binding,
                        "host_session_id": a["binding"]["session_id"],
                    },
                )
                self.service.control_ack(t["id"], payload["control_epoch"])
            if kind == "stop_external" or payload.get("action") == "cancel":
                for o in a["snapshot"]["external_operations"]:
                    if o["status"] not in {"succeeded", "failed", "cancelled"}:
                        adapter = self.service.governance[o["provider"]]
                        await asyncio.to_thread(adapter.stop, o)
