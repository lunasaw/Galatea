"""Administrator-installed native Host ToolProvider factory."""

from pathlib import Path
import httpx
from codex_agent.providers import ToolProvider
from codex_agent.registry import ToolResult
from galatea_app.contracts import AGENT_TOOLS


def create_provider(options):
    credential = Path(options["credential_file"])
    if credential.stat().st_mode & 0o077:
        raise ValueError("Provider credential must be private")
    client = httpx.Client(
        base_url=options["service_url"],
        headers={"Authorization": "Bearer " + credential.read_text().strip()},
        timeout=30,
    )

    def context(ctx):
        return {
            k: getattr(ctx, k)
            for k in ("principal_id", "session_id", "thread_id", "turn_id", "call_id")
        } | {
            "host_id": options["host_id"],
            "workspace_ref": options["workspace_ref"],
            "role": "agent",
        }

    def handler(name):
        def call(ctx, args):
            if ctx.cancel.is_set():
                return ToolResult(False, error={"code": "CANCELLED"})
            response = client.post(
                "/internal/v1/tool",
                json={"name": name, "arguments": args, "context": context(ctx)},
            )
            data = response.json()
            if response.is_success and name in {"start_task", "get_current_task"} and data.get("task_id"):
                # A native Provider result may present an installed MCP resource even
                # when this runtime does not expose MCP entry tools to the model.
                data = {**data, "app_view": {
                    "server": options.get("app_server", "galatea_app"),
                    "resource_uri": "ui://galatea/workbench/v1",
                    "arguments": {"task_id": data["task_id"]},
                }}
            return ToolResult(
                response.is_success,
                data=data if response.is_success else None,
                error=None if response.is_success else data,
            )

        return call

    def observe(identity):
        args = identity.get("arguments", {})
        if not args.get("command_id"):
            return None
        trusted = {
            k: identity[k]
            for k in ("principal_id", "session_id", "thread_id", "turn_id", "call_id")
        } | {
            "host_id": options["host_id"],
            "workspace_ref": options["workspace_ref"],
            "role": "agent",
        }
        response = client.post(
            "/internal/v1/receipt",
            json={"context": trusted, "command_id": args["command_id"]},
        )
        return response.json() if response.is_success else None

    return ToolProvider(
        handlers={name: handler(name) for name in AGENT_TOOLS},
        observe_receipt=observe,
        close=client.close,
    )
