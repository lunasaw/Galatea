"""Existing Campaign tool contract; mutating observation needs explicit recovery scope."""

import json
from galatea_app.common import require, digest


class GovernanceMCP:
    def __init__(
        self,
        call,
        *,
        project_id,
        campaign_id,
        authorization_check,
        recovery_allowed=False,
        exclusive_campaign=False
    ):
        self.call, self.project_id, self.campaign_id = call, project_id, campaign_id
        self.authorization_check = authorization_check
        self.recovery_allowed = recovery_allowed
        self.exclusive_campaign = exclusive_campaign
        self.scope = {"project_id": project_id, "campaign_id": campaign_id}

    def authorized(self, proposal):
        return (
            proposal["immutable_inputs"]["route"] == "governance_mcp"
            and proposal["immutable_inputs"]["project_id"] == self.project_id
            and self.authorization_check(proposal)
        )

    def owns(self, intent, operation):
        inputs = intent["immutable_inputs"]
        if inputs.get("campaign_id") != self.campaign_id:
            return False
        expected = (
            "op-"
            + digest(
                {
                    "schema_version": "galatea.operation/v1",
                    "campaign_id": self.campaign_id,
                    "step_id": inputs.get("step_id"),
                    "attempt": inputs.get("attempt"),
                }
            )[:32]
        )
        if operation["id"] != expected:
            return False
        result = self.call(
            "galatea_get_operation", {**self.scope, "operation_id": operation["id"]}
        )
        return result.get("ok", False) and all(
            result.get("data", {}).get(k) == inputs.get(k)
            for k in ("step_id", "attempt", "role", "release_id", "config_id")
        )

    def reconcile(self, intent):
        inputs = intent["immutable_inputs"]
        expected = (
            "op-"
            + digest(
                {
                    "schema_version": "galatea.operation/v1",
                    "campaign_id": self.campaign_id,
                    "step_id": inputs.get("step_id"),
                    "attempt": inputs.get("attempt"),
                }
            )[:32]
        )
        ref = {"id": expected, "campaign_id": self.campaign_id}
        return ref if self.owns(intent, ref) else None

    def observe(self, operation):
        tool = (
            "galatea_observe_job" if self.recovery_allowed else "galatea_get_operation"
        )
        result = self.call(tool, {**self.scope, "operation_id": operation["id"]})
        require(result.get("ok"), "UNAVAILABLE")
        data = result["data"]
        status = data.get("execution", "unknown")
        return {
            "status": (
                {
                    "pending": "accepted",
                    "stopping": "running",
                    "stopped": "cancelled",
                }.get(status, status)
                if status
                in {
                    "pending",
                    "queued",
                    "running",
                    "stopping",
                    "stopped",
                    "succeeded",
                    "failed",
                    "cancelled",
                    "unknown",
                }
                else "unknown"
            ),
            "operation": data,
            "run_id": data.get("run_id"),
            "fresh": self.recovery_allowed,
        }

    def stop(self, operation):
        require(
            self.exclusive_campaign and self.recovery_allowed,
            "AUTHORIZATION_REQUIRED",
            "Task must own Campaign cancellation and recovery scope",
        )
        self.call(
            "galatea_cancel_campaign",
            {**self.scope, "reason": "Task cancellation barrier"},
        )
        return self.call(
            "galatea_stop_job",
            {
                **self.scope,
                "operation_id": operation["id"],
                "reason": "Task cancellation",
            },
        )

    def read_artifact(self, ref, max_bytes):
        require(
            ref.get("route") == "governance_mcp"
            and ref.get("campaign_id") == self.campaign_id,
            "FORBIDDEN",
        )
        result = self.call(
            "galatea_get_artifact",
            {**self.scope, "run_id": ref["run_id"], "artifact_ref": ref["path"]},
        )
        require(result.get("ok"), "EVIDENCE_REJECTED")
        # This API serves evidence reports or verified artifact metadata, not binary bytes.
        data = json.dumps(result["data"], ensure_ascii=False, sort_keys=True).encode()
        require(len(data) <= max_bytes, "RATE_LIMITED")
        return data


class MCPTransport:
    """Fixed stateless JSON MCP endpoint; no retries of ambiguous tool calls."""

    def __init__(self, url, token, *, transport=None):
        import httpx
        from urllib.parse import urlparse

        parsed = urlparse(url)
        require(
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not (
                parsed.username or parsed.password or parsed.query or parsed.fragment
            ),
            "INVALID_COMMAND",
        )
        self.url = url
        self.client = httpx.Client(
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-03-26",
            },
            timeout=30,
            follow_redirects=False,
            transport=transport,
        )

    def __call__(self, name, arguments):
        from uuid import uuid4

        with self.client.stream(
            "POST",
            self.url,
            json={
                "jsonrpc": "2.0",
                "id": str(uuid4()),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        ) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                require(len(data) <= 1024 * 1024, "RATE_LIMITED")
            result = json.loads(data)
        require("result" in result, "UNAVAILABLE")
        payload = result["result"]
        if "structuredContent" in payload:
            return payload["structuredContent"]
        return json.loads(
            next(
                c["text"] for c in payload.get("content", []) if c.get("type") == "text"
            )
        )

    def close(self):
        self.client.close()
