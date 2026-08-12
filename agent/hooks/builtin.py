"""Built-in hooks for logging, policy, context hygiene, and validation."""

from __future__ import annotations

import json
import logging
from typing import Any

from agent.hooks.types import HookContext, HookInput, HookOutput
from agent.policies.permission import DEFAULT_DISALLOWED_TOOLS, PermissionPolicy

logger = logging.getLogger(__name__)

MAX_TOOL_OUTPUT_CHARS = 6000


async def logging_hook(input_data: HookInput, context: HookContext) -> HookOutput:
    """Log hook activity in a compact structured form."""
    event = {
        "event": input_data.event.value,
        "session_id": context.session_id,
        "agent_type": context.agent_type,
        "tool_name": input_data.tool_name,
        "tool_use_id": input_data.tool_use_id,
    }
    logger.info("GALATEA_HOOK %s", json.dumps(event, sort_keys=True, default=str))
    return HookOutput()


async def cost_tracking_hook(input_data: HookInput, context: HookContext) -> HookOutput:
    """Accumulate cost and token metadata from result-like hook payloads."""
    data = input_data.data or {}
    cost = float(data.get("total_cost_usd") or data.get("cost_usd") or 0.0)
    usage = data.get("usage") or {}
    tokens = int(
        data.get("tokens")
        or usage.get("input_tokens", 0)
        + usage.get("output_tokens", 0)
    )
    metadata = context.metadata or {}
    metadata["total_cost_usd"] = float(metadata.get("total_cost_usd", 0.0)) + cost
    metadata["total_tokens"] = int(metadata.get("total_tokens", 0)) + tokens
    return HookOutput(metadata={"total_cost_usd": metadata["total_cost_usd"], "total_tokens": metadata["total_tokens"]})


async def audit_hook(input_data: HookInput, context: HookContext) -> HookOutput:
    """Record an in-memory audit event in hook context metadata."""
    metadata = context.metadata or {}
    audit_events = metadata.setdefault("audit_events", [])
    audit_events.append(
        {
            "event": input_data.event.value,
            "session_id": context.session_id,
            "agent_type": context.agent_type,
            "project_name": context.project_name,
            "tool_name": input_data.tool_name,
            "tool_use_id": input_data.tool_use_id,
        }
    )
    return HookOutput()


async def validation_hook(input_data: HookInput, context: HookContext) -> HookOutput:
    """Validate basic tool call shape."""
    if input_data.tool_name and input_data.tool_input is not None and not isinstance(input_data.tool_input, dict):
        return HookOutput(
            permission_decision="deny",
            permission_decision_reason="Tool input must be a JSON object.",
            reason="Invalid tool input shape",
        )
    return HookOutput()


def make_permission_hook(policy: PermissionPolicy):
    """Build a PreToolUse hook from a PermissionPolicy."""

    async def permission_hook(input_data: HookInput, context: HookContext) -> HookOutput:
        tool_name = input_data.tool_name or ""
        tool_input = input_data.tool_input or {}
        behavior = policy.check_permission(tool_name, tool_input)
        reason = policy.explain_permission(tool_name, tool_input)
        if behavior == "allow":
            return HookOutput(
                permission_decision="allow",
                permission_decision_reason=reason,
                reason=reason,
            )
        if behavior == "ask":
            return HookOutput(
                permission_decision="ask",
                permission_decision_reason=reason,
                reason=reason,
            )
        return HookOutput(
            permission_decision="deny",
            permission_decision_reason=reason,
            reason=reason,
        )

    return permission_hook


async def deny_builtin_mutation_hook(input_data: HookInput, context: HookContext) -> HookOutput:
    """Deny generic mutation and shell tools by default."""
    tool_name = input_data.tool_name or ""
    if tool_name in DEFAULT_DISALLOWED_TOOLS:
        return HookOutput(
            permission_decision="deny",
            permission_decision_reason=f"{tool_name} is disabled for Galatea platform agents.",
            reason="Built-in mutation tool denied",
        )
    return HookOutput()


async def summarize_large_tool_output_hook(input_data: HookInput, context: HookContext) -> HookOutput:
    """Trim large tool output before it enters model context."""
    response = input_data.tool_response
    response_text = _response_to_text(response)
    if len(response_text) <= MAX_TOOL_OUTPUT_CHARS:
        return HookOutput()

    summary = {
        "truncated": True,
        "original_chars": len(response_text),
        "summary": response_text[:MAX_TOOL_OUTPUT_CHARS] + "\n...[truncated by Galatea hook]...",
    }
    return HookOutput(
        additional_context="Large tool output was truncated. Use artifact/log URIs for full content.",
        updated_mcp_tool_output={"content": [{"type": "text", "text": json.dumps(summary, ensure_ascii=False)}]},
    )


async def classify_tool_failure_hook(input_data: HookInput, context: HookContext) -> HookOutput:
    """Attach recoverability guidance after tool failures."""
    error = str(input_data.data.get("error", ""))
    if "permission" in error.lower() or "denied" in error.lower():
        guidance = "Permission failure: request approval or use a narrower read-only tool."
    elif "timeout" in error.lower():
        guidance = "Timeout: poll job status or reduce the requested work."
    else:
        guidance = "Tool failed: inspect returned error summary before retrying."
    return HookOutput(additional_context=guidance, reason=guidance)


async def compact_context_hook(input_data: HookInput, context: HookContext) -> HookOutput:
    """Add compaction instructions when Claude Code compacts context."""
    instructions = (
        "Preserve Galatea invariants during compaction: stage_run_id, Ray job IDs, "
        "MLflow run IDs, artifact URIs/digests, approvals, permission denials, "
        "objective metric/direction, and unresolved errors. Drop bulky logs, raw "
        "samples, duplicated tool output, and sensitive values."
    )
    return HookOutput(
        system_message=instructions,
        reason="Preserve execution evidence and remove bulky logs/samples.",
        additional_context=instructions,
    )


def _response_to_text(response: Any) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    try:
        return json.dumps(response, ensure_ascii=False, default=str)
    except TypeError:
        return str(response)
