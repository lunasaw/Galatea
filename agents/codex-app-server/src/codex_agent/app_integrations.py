"""Opt-in app integration configuration and authenticated background service routes."""

import asyncio
import json
from pathlib import Path
import secrets

from .app_gateway import AppGateway, ProofSigner
from .background_apps import BackgroundApps
from .console.models import EventQuery


def validate_config(value):
    if not isinstance(value, dict) or set(value) - {
        "enabled",
        "host_id",
        "key_id",
        "private_key_file",
        "service_credentials",
        "servers",
        "workspaces",
    }:
        raise ValueError("invalid app_integrations configuration")
    if not value.get("enabled", False):
        return
    if set(value) != {
        "enabled",
        "host_id",
        "key_id",
        "private_key_file",
        "service_credentials",
        "servers",
        "workspaces",
    }:
        raise ValueError("app integrations require explicit signing, identities and scopes")
    if not value["workspaces"] or not value["service_credentials"] or not value["servers"]:
        raise ValueError("empty app integration scopes")
    for path in [
        value["private_key_file"],
        *value["service_credentials"].values(),
        *value["workspaces"].values(),
    ]:
        if not isinstance(path, str) or not Path(path).is_absolute() or ".." in Path(path).parts:
            raise ValueError("integration paths must be absolute")
    for server in value["servers"].values():
        if set(server) != {"audience", "resources", "tools"}:
            raise ValueError("invalid registered app server")
        for tool in server["tools"].values():
            if set(tool) != {"read_only"} or not isinstance(tool["read_only"], bool):
                raise ValueError("explicit tool access required")


def protected(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("integration credential must be a protected file")
    return path.read_bytes()


class AppIntegrations:
    def __init__(self, host, events, config):
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        validate_config(config)
        key = load_pem_private_key(protected(config["private_key_file"]), password=None)
        self.host, self.events, self.config = host, events, config
        self.gateway = AppGateway(
            host,
            ProofSigner(key, config["key_id"], config["host_id"], host.principal_id),
            servers=config["servers"],
            workspace_refs={str(Path(v).resolve()): k for k, v in config["workspaces"].items()},
        )
        self.background = BackgroundApps(host, workspaces=config["workspaces"])
        self.gateway.workspace_refs.update(
            {str(path): ref for ref, path in self.background.workspaces.items()}
        )
        self.credentials = {
            service: protected(path).decode().strip()
            for service, path in config["service_credentials"].items()
        }
        if any(len(token) < 32 for token in self.credentials.values()):
            raise ValueError("integration credential too short")
        self.runner = None

    def authenticate(self, headers):
        bearer = headers.get("authorization", "").removeprefix("Bearer ")
        for service, token in self.credentials.items():
            if secrets.compare_digest(bearer, token):
                return service
        raise PermissionError("service authentication required")

    async def start(self):
        async def loop():
            while True:
                try:
                    await self.background.tick()
                except Exception:
                    pass  # Records remain durable; readiness and receipts expose unresolved state.
                await asyncio.sleep(1)

        self.runner = asyncio.create_task(loop(), name="integration-continuations")

    async def close(self):
        if self.runner:
            self.runner.cancel()
            await asyncio.gather(self.runner, return_exceptions=True)

    async def route(self, method, path, body, headers, query):
        service = self.authenticate(headers)
        if path == "/internal/v1/continuation-bindings" and method == "POST":
            return self.background.bind(body, service)
        if path == "/internal/v1/continuations" and method == "POST":
            return self.background.accept(body, service)
        if path.startswith("/internal/v1/continuation-bindings/") and method == "POST":
            return self.background.revoke(path.rsplit("/", 1)[1], service)
        if path.startswith("/internal/v1/continuations/"):
            id = path.rsplit("/", 1)[1]
            return (
                self.background.cancel(id, service)
                if method == "POST"
                else self.background.get(id, service)
            )
        if path == "/internal/v1/app-activations" and method == "POST":
            result = await self.background.activate(body, service)
            if result["state"] == "ready":
                workspace = self.background.workspaces[result["workspace_ref"]]
                self.gateway.workspace_refs[str(workspace)] = result["workspace_ref"]
            return result
        if path.startswith("/internal/v1/app-activations/") and method == "GET":
            return self.background._read("app-activations", path.rsplit("/", 1)[1], service)
        if path == "/internal/v1/interrupt" and method == "POST":
            binding = self.background._read("continuation-bindings", body["binding_id"], service)
            if binding["host_session_id"] != body["host_session_id"]:
                raise ValueError("interrupt scope mismatch")
            session = self.host.get_session(body["host_session_id"])
            if session.get("active_turn_id"):
                await self.host.interrupt(body["host_session_id"])
            return {"state": "interrupted", "session_id": body["host_session_id"]}
        if path == "/internal/v1/runtime-events" and method == "GET":
            binding = self.background._read(
                "continuation-bindings", query["binding_id"][0], service
            )
            batch = await self.events.read_events(
                EventQuery(
                    session_id=binding["host_session_id"],
                    after=query.get("after", [None])[0],
                    limit=100,
                )
            )
            from .app_event_export import export_events

            exported = export_events(
                self.host.store, binding, self.config["host_id"], batch["events"]
            )
            return {**{k: v for k, v in batch.items() if k != "events"}, "events": exported}
        raise ValueError("unknown integration endpoint")
