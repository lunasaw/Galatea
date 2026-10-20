"""Generic durable integration inbox. Dispatch uses the sole native Host runtime."""

from __future__ import annotations
import asyncio
import json
from datetime import datetime
from pathlib import Path
import time
from .state import identifier, payload_digest


def epoch(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class BackgroundApps:
    def __init__(self, host, *, workspaces):
        self.host, self.store, self.workspaces = (
            host,
            host.store,
            {k: Path(v).resolve() for k, v in workspaces.items()},
        )
        self.lock = asyncio.Lock()
        for path in (self.store.root / "app-activations").glob("*.json"):
            row = self.store.read("app-activations/" + path.name)
            if row.get("state") == "ready":
                session = self.host.get_session(row["session_id"])
                if session and session.get("cwd"):
                    self.workspaces[row["workspace_ref"]] = Path(session["cwd"]).resolve()

    def _read(self, kind, id, service):
        row = self.store.read(f"{kind}/{identifier(id)}.json")
        if not row or row["service"] != service:
            raise ValueError("integration object unavailable")
        return row

    def bind(self, body, service):
        id = "binding-" + identifier(body["request_id"])
        path = f"continuation-bindings/{id}.json"
        previous = self.store.read(path)
        if previous:
            if previous["service"] != service or previous["digest"] != payload_digest(body):
                raise ValueError("binding conflict")
            return previous
        receipt = self.store.read(f"app-calls/{identifier(body['launch_receipt'])}.json")
        activation = self.store.read(f"app-activations/{identifier(body['launch_receipt'])}.json")
        authorized = (
            receipt
            and receipt["state"] == "completed"
            and receipt["principal_id"] == self.host.principal_id
            and receipt["session_id"] == body["host_session_id"]
            and receipt["write"]
        )
        authorized = authorized or (
            activation
            and activation["state"] == "ready"
            and activation["service"] == service
            and activation["session_id"] == body["host_session_id"]
        )
        if not authorized:
            raise ValueError("binding requires a trusted launch receipt for this session")
        if (
            not 0 < int(body["max_continuations"]) <= 100
            or epoch(body["expires_at"]) <= time.time()
        ):
            raise ValueError("invalid continuation budget")
        session = self.host.get_session(body["host_session_id"])
        row = {
            **body,
            "binding_id": id,
            "service": service,
            "digest": payload_digest(body),
            "thread_id": session["thread_id"],
            "state": "active",
            "count": 0,
        }
        self.store.write(path, row)
        return row

    def accept(self, body, service):
        id = identifier(body["request_id"])
        path = f"continuations/{id}.json"
        previous = self.store.read(path)
        if previous:
            if previous["service"] != service or previous["digest"] != payload_digest(body):
                raise ValueError("continuation conflict")
            return previous
        binding = self._read("continuation-bindings", body["binding_id"], service)
        # The durable inbox is authoritative if a crash lost the cached counter.
        committed = sum(
            1
            for p in (self.store.root / "continuations").glob("*.json")
            if (self.store.read("continuations/" + p.name) or {}).get("binding_id")
            == binding["binding_id"]
        )
        binding["count"] = max(binding["count"], committed)
        if (
            binding["state"] != "active"
            or binding["host_session_id"] != body["host_session_id"]
            or epoch(binding["expires_at"]) <= time.time()
            or binding["count"] >= binding["max_continuations"]
            or epoch(body["expires_at"]) > epoch(binding["expires_at"])
            or body["context"].get("kind") != "untrusted"
        ):
            raise ValueError("continuation scope expired or exhausted")
        row = {
            **body,
            "service": service,
            "digest": payload_digest(body),
            "state": "queued",
            "receipt_id": id,
        }
        self.store.write(path, row)
        binding["count"] += 1
        self.store.write(f"continuation-bindings/{binding['binding_id']}.json", binding)
        return row

    def get(self, id, service):
        return self._read("continuations", id, service)

    def binding_status(self, id, service):
        """Scoped runtime facts, without prompts, credentials, or authority changes."""
        binding = self._read("continuation-bindings", id, service)
        session = self.host.get_session(binding["host_session_id"]) or {}
        pending = False
        for path in (self.store.root / "continuations").glob("*.json"):
            row = self.store.read("continuations/" + path.name)
            if row.get("binding_id") != id:
                continue
            state = row["state"]
            if state in {"dispatching", "unknown", "started"}:
                receipt = self.store.read(f"requests/{row['request_id']}.json") or {}
                if receipt.get("status") in {"completed", "failed", "interrupted"}:
                    continue
            if state in {"queued", "accepted", "dispatching", "unknown", "started"}:
                pending = True
                break
        return {
            "binding_id": id,
            "state": binding["state"] if epoch(binding["expires_at"]) > time.time() else "expired",
            "session_id": binding["host_session_id"],
            "thread_id": session.get("thread_id"),
            "session_status": session.get("status", "unavailable"),
            "active_turn_id": session.get("active_turn_id"),
            "last_turn_id": session.get("last_turn_id"),
            "last_turn_status": session.get("last_turn_status"),
            "pending_continuation": pending,
        }

    def revoke(self, id, service):
        binding = self._read("continuation-bindings", id, service)
        binding["state"] = "revoked"
        self.store.write(f"continuation-bindings/{identifier(id)}.json", binding)
        return binding

    def cancel(self, id, service):
        row = self.get(id, service)
        if row["state"] in ("queued", "accepted"):
            row["state"] = "cancelled"
            self.store.write(f"continuations/{identifier(id)}.json", row)
        return row

    async def tick(self):
        async with self.lock:
            root = self.store.root / "continuations"
            for file in sorted(root.glob("*.json")):
                row = self.store.read("continuations/" + file.name)
                state = row["state"]
                if state in ("dispatching", "unknown", "started"):
                    native = self.store.read(f"requests/{row['request_id']}.json")
                    if native:
                        row["state"] = {
                            "accepted": "started",
                            "completed": "completed",
                            "interrupted": "completed",
                            "failed": "completed",
                        }.get(native["status"], "unknown")
                        row["native_receipt"] = native
                        self.store.write("continuations/" + file.name, row)
                    elif state == "dispatching":
                        row["state"] = "unknown"
                        self.store.write("continuations/" + file.name, row)
                    continue
                if state not in ("queued", "accepted"):
                    continue
                binding = self._read("continuation-bindings", row["binding_id"], row["service"])
                if binding["state"] != "active":
                    row["state"] = "cancelled"
                elif (
                    epoch(row["expires_at"]) <= time.time()
                    or epoch(binding["expires_at"]) <= time.time()
                ):
                    row["state"] = "expired"
                elif epoch(row["not_before"]) > time.time():
                    continue
                else:
                    session = self.host.get_session(row["host_session_id"])
                    if session["status"] != "ready":
                        continue
                    if session["thread_id"] != binding["thread_id"]:
                        row["state"] = "unknown"
                    else:
                        row["state"] = "dispatching"
                        self.store.write("continuations/" + file.name, row)
                        try:
                            result = await self.host.send_message(
                                row["host_session_id"],
                                row["request_id"],
                                "A registered background source has new facts. Read its untrusted context and verify current state before acting.",
                                options={
                                    "additionalContext": {
                                        "background": {
                                            "kind": "untrusted",
                                            "value": json.dumps(row["context"], ensure_ascii=False),
                                        }
                                    }
                                },
                            )
                            row["state"] = (
                                "started"
                                if result["status"] == "accepted"
                                else (
                                    "completed"
                                    if result["status"] in ("completed", "failed", "interrupted")
                                    else "unknown"
                                )
                            )
                            row["native_receipt"] = result
                        except Exception:
                            row["state"] = "unknown"
                self.store.write("continuations/" + file.name, row)

    async def activate(self, body, service):
        id = identifier(body["activation_id"])
        path = f"app-activations/{id}.json"
        previous = self.store.read(path)
        if previous:
            if previous["service"] != service or previous["digest"] != payload_digest(body):
                raise ValueError("activation conflict")
            return previous
        receipt = self.store.read(f"app-calls/{identifier(body['launch_receipt'])}.json")
        if (
            not receipt
            or receipt["state"] != "completed"
            or receipt["principal_id"] != self.host.principal_id
            or not receipt["write"]
        ):
            raise ValueError("trusted launch confirmation required")
        workspace = self.workspaces.get(body.get("workspace_ref"))
        args = receipt["identity"]["arguments"]
        explicit = (
            args.get("activation_id") == id and args.get("workspace_ref") == body["workspace_ref"]
        )
        delegated = (
            args.get("command_id") == body.get("source_command_id")
            and receipt.get("workspace_ref") in self.workspaces
        )
        if (
            workspace is None
            and delegated
            and body.get("parent_workspace_ref") == receipt.get("workspace_ref")
        ):
            root = self.workspaces[body["parent_workspace_ref"]]
            relative = Path(body.get("workspace_relative_path", ""))
            candidate = (root / relative).resolve()
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not candidate.is_relative_to(root)
                or not candidate.is_dir()
            ):
                raise ValueError("unsafe activation workspace")
            workspace = candidate
        if workspace is None or not (explicit or delegated):
            raise ValueError("activation does not match launch scope")
        self.workspaces[body["workspace_ref"]] = workspace
        row = {
            **body,
            "service": service,
            "digest": payload_digest(body),
            "state": "binding",
            "source_session_id": receipt["session_id"],
            "app": {k: receipt["identity"].get(k) for k in ("server", "uri", "instanceId")},
        }
        self.store.write(path, row)
        try:
            if body.get("existing_session_ref"):
                session = self.host.get_session(body["existing_session_ref"])
                if Path(session.get("cwd", "")).resolve() != workspace:
                    raise ValueError("session workspace mismatch")
            else:
                session = await self.host.create_session(
                    "app-" + id, options={"cwd": str(workspace)}
                )
            row.update(
                state="ready",
                session_id=session["id"],
                thread_id=session["thread_id"],
                workspace_ref=body["workspace_ref"],
            )
        except Exception:
            row["state"] = "unknown"
        self.store.write(path, row)
        return row
