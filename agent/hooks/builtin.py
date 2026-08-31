"""SDK-native hook callbacks for Galatea safety and audit evidence."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from claude_agent_sdk import HookCallback, HookContext, HookInput, HookJSONOutput

from agent.hooks.types import GalateaHookContext
from agent.policies.permission import PermissionPolicy

logger = logging.getLogger(__name__)

MAX_TOOL_OUTPUT_CHARS = 6000


def make_logging_hook(runtime_context: GalateaHookContext) -> HookCallback:
    """Create a compact structured SDK hook logger."""

    async def logging_hook(
        input_data: HookInput,
        tool_use_id: str | None,
        sdk_context: HookContext,
    ) -> HookJSONOutput:
        del sdk_context
        event = {
            "event": input_data["hook_event_name"],
            "session_id": input_data.get("session_id", runtime_context.session_id),
            "agent_type": input_data.get("agent_type", runtime_context.agent_type),
            "tool_name": input_data.get("tool_name"),
            "tool_use_id": tool_use_id or input_data.get("tool_use_id"),
        }
        logger.info("GALATEA_HOOK %s", json.dumps(event, sort_keys=True, default=str))
        return {}

    return logging_hook


def make_audit_hook(runtime_context: GalateaHookContext) -> HookCallback:
    """Record hook activity in the runtime's application audit context."""

    async def audit_hook(
        input_data: HookInput,
        tool_use_id: str | None,
        sdk_context: HookContext,
    ) -> HookJSONOutput:
        del sdk_context
        runtime_context.metadata.setdefault("audit_events", []).append(
            {
                "event": input_data["hook_event_name"],
                "session_id": input_data.get("session_id", runtime_context.session_id),
                "agent_type": input_data.get("agent_type", runtime_context.agent_type),
                "agent_id": input_data.get("agent_id"),
                "project_name": runtime_context.project_name,
                "tool_name": input_data.get("tool_name"),
                "tool_use_id": tool_use_id or input_data.get("tool_use_id"),
            }
        )
        return {}

    return audit_hook


async def validation_hook(
    input_data: HookInput,
    tool_use_id: str | None,
    sdk_context: HookContext,
) -> HookJSONOutput:
    """Reject malformed tool input before execution."""
    del tool_use_id, sdk_context
    tool_input = input_data.get("tool_input")
    if input_data.get("tool_name") and tool_input is not None and not isinstance(tool_input, dict):
        return _pre_tool_decision("deny", "Tool input must be a JSON object.")
    return {}


def make_permission_hook(policy: PermissionPolicy) -> HookCallback:
    """Apply only Galatea-specific rules and defer everything else to the SDK."""

    async def permission_hook(
        input_data: HookInput,
        tool_use_id: str | None,
        sdk_context: HookContext,
    ) -> HookJSONOutput:
        del tool_use_id, sdk_context
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})
        behavior = policy.check_permission(tool_name, tool_input)
        if behavior == "defer":
            return _pre_tool_decision("defer", policy.explain_permission(tool_name, tool_input))
        return _pre_tool_decision(
            behavior,
            policy.explain_permission(tool_name, tool_input),
        )

    return permission_hook


def make_permission_request_audit_hook(runtime_context: GalateaHookContext) -> HookCallback:
    """Record SDK permission requests without deciding them locally."""

    async def permission_request_audit_hook(
        input_data: HookInput,
        tool_use_id: str | None,
        sdk_context: HookContext,
    ) -> HookJSONOutput:
        del sdk_context
        request_id = f"approval-{uuid.uuid4()}"
        runtime_context.metadata.setdefault("approval_requests", []).append(
            {
                "approval_request_id": request_id,
                "session_id": input_data.get("session_id", runtime_context.session_id),
                "agent_id": input_data.get("agent_id"),
                "tool_use_id": tool_use_id,
                "tool_name": input_data.get("tool_name"),
                "scope": dict(input_data.get("tool_input", {})),
                "reason": "Claude Code permission rules require an explicit decision.",
                "persistence_options": list(input_data.get("permission_suggestions", [])),
                "status": "requested",
            }
        )
        return {}

    return permission_request_audit_hook


def make_summarize_large_tool_output_hook(
    max_chars: int = MAX_TOOL_OUTPUT_CHARS,
) -> HookCallback:
    """Trim large MCP output before it enters model context."""

    async def summarize_large_tool_output_hook(
        input_data: HookInput,
        tool_use_id: str | None,
        sdk_context: HookContext,
    ) -> HookJSONOutput:
        del tool_use_id, sdk_context
        response = input_data.get("tool_response")
        response_text = _response_to_text(response)
        if len(response_text) <= max_chars:
            return {}

        summary = {
            "truncated": True,
            "original_chars": len(response_text),
            "summary": response_text[:max_chars] + "\n...[truncated by Galatea hook]...",
        }
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    "Large tool output was truncated. Use artifact/log URIs for full content."
                ),
                "updatedMCPToolOutput": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(summary, ensure_ascii=False),
                        }
                    ]
                },
            }
        }

    return summarize_large_tool_output_hook


async def classify_tool_failure_hook(
    input_data: HookInput,
    tool_use_id: str | None,
    sdk_context: HookContext,
) -> HookJSONOutput:
    """Attach recoverability guidance after tool failures."""
    del tool_use_id, sdk_context
    error = str(input_data.get("error", ""))
    if "permission" in error.lower() or "denied" in error.lower():
        guidance = "Permission failure: request approval or use a narrower read-only tool."
    elif "timeout" in error.lower():
        guidance = "Timeout: poll job status or reduce the requested work."
    else:
        guidance = "Tool failed: inspect returned error summary before retrying."
    return {
        "reason": guidance,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUseFailure",
            "additionalContext": guidance,
        },
    }


async def compact_context_hook(
    input_data: HookInput,
    tool_use_id: str | None,
    sdk_context: HookContext,
) -> HookJSONOutput:
    """Preserve durable execution evidence during SDK context compaction."""
    del input_data, tool_use_id, sdk_context
    instructions = (
        "Preserve Galatea invariants during compaction: stage_run_id, Ray job IDs, "
        "MLflow run IDs, artifact URIs/digests, approvals, permission denials, "
        "objective metric/direction, and unresolved errors. Drop bulky logs, raw "
        "samples, duplicated tool output, and sensitive values."
    )
    return {
        "systemMessage": instructions,
        "reason": "Preserve execution evidence and remove bulky logs/samples.",
    }


def _pre_tool_decision(decision: str, reason: str) -> HookJSONOutput:
    return {
        "reason": reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        },
    }


def _response_to_text(response: Any) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    try:
        return json.dumps(response, ensure_ascii=False, default=str)
    except TypeError:
        return str(response)
