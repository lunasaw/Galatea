"""Hook registry and Claude SDK hook adapter."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from claude_agent_sdk import HookMatcher as SDKHookMatcher

from agent.hooks.types import (
    HookCallback,
    HookContext,
    HookEvent,
    HookInput,
    HookMatcher,
    HookOutput,
    HookRegistry,
)


class HookManager:
    """High-level hook management and SDK conversion helpers."""

    def __init__(self, context: Optional[HookContext] = None) -> None:
        self.registry = HookRegistry()
        self.context = context or HookContext(session_id="default", agent_type="default")

    def add_hook(
        self,
        event: HookEvent,
        callback: HookCallback,
        matcher: str | None = None,
        timeout: float | None = None,
    ) -> None:
        """Register a hook callback."""
        self.registry.register(
            event,
            HookMatcher(matcher=matcher, hooks=[callback], timeout=timeout),
        )

    def add_pre_tool_use_hook(
        self,
        callback: HookCallback,
        tool_name_pattern: str = None,
    ) -> None:
        self.add_hook(HookEvent.PRE_TOOL_USE, callback, tool_name_pattern)

    def add_post_tool_use_hook(
        self,
        callback: HookCallback,
        tool_name_pattern: str = None,
    ) -> None:
        self.add_hook(HookEvent.POST_TOOL_USE, callback, tool_name_pattern)

    def add_session_start_hook(self, callback: HookCallback) -> None:
        self.add_hook(HookEvent.SESSION_START, callback)

    async def invoke_hooks(
        self,
        event: HookEvent,
        input_data: HookInput,
        context: HookContext | None = None,
    ) -> List[HookOutput]:
        return await self.registry.invoke(event, input_data, context or self.context)

    def clear_hooks(self, event: HookEvent = None) -> None:
        self.registry.clear(event)

    def to_sdk_hooks(self) -> Dict[str, List[SDKHookMatcher]]:
        """Convert local hooks to Claude SDK hook matchers."""
        sdk_hooks: Dict[str, List[SDKHookMatcher]] = {}
        for event in HookEvent:
            matchers = self.registry.get_hooks(event)
            if not matchers:
                continue
            sdk_hooks[event.value] = [
                SDKHookMatcher(
                    matcher=matcher.matcher,
                    hooks=[
                        _make_sdk_callback(event, callback, self.context)
                        for callback in matcher.hooks
                    ],
                    timeout=matcher.timeout,
                )
                for matcher in matchers
            ]
        return sdk_hooks


def _make_sdk_callback(
    event: HookEvent,
    callback: HookCallback,
    base_context: HookContext,
):
    async def _sdk_callback(
        input_data: Dict[str, Any],
        tool_use_id: str | None,
        sdk_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        local_input = _to_local_input(event, input_data, tool_use_id)
        local_context = HookContext(
            session_id=input_data.get("session_id") or base_context.session_id,
            agent_type=input_data.get("agent_type") or base_context.agent_type,
            project_name=base_context.project_name,
            turn_number=base_context.turn_number,
            metadata={
                **(base_context.metadata or {}),
                "sdk_context": sdk_context,
                "transcript_path": input_data.get("transcript_path"),
                "cwd": input_data.get("cwd"),
                "agent_id": input_data.get("agent_id"),
            },
        )
        output = callback(local_input, local_context)
        if hasattr(output, "__await__"):
            output = await output
        return _to_sdk_output(event, output)

    return _sdk_callback


def _to_local_input(
    event: HookEvent,
    input_data: Dict[str, Any],
    tool_use_id: str | None,
) -> HookInput:
    return HookInput(
        event=event,
        data=input_data,
        tool_name=input_data.get("tool_name"),
        tool_input=input_data.get("tool_input"),
        tool_response=input_data.get("tool_response"),
        tool_use_id=tool_use_id or input_data.get("tool_use_id"),
    )


def _to_sdk_output(event: HookEvent, output: HookOutput) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "continue_": output.continue_,
    }
    if output.stop_reason:
        result["stopReason"] = output.stop_reason
    if output.system_message:
        result["systemMessage"] = output.system_message
    if output.reason:
        result["reason"] = output.reason

    hook_specific: Dict[str, Any] = {"hookEventName": event.value}
    if output.additional_context:
        hook_specific["additionalContext"] = output.additional_context
    if event == HookEvent.PRE_TOOL_USE:
        if output.permission_decision:
            hook_specific["permissionDecision"] = output.permission_decision
        if output.permission_decision_reason:
            hook_specific["permissionDecisionReason"] = output.permission_decision_reason
    if event == HookEvent.POST_TOOL_USE:
        if output.updated_tool_output is not None:
            hook_specific["updatedToolOutput"] = output.updated_tool_output
        if output.updated_mcp_tool_output is not None:
            hook_specific["updatedMCPToolOutput"] = output.updated_mcp_tool_output

    events_with_hook_specific = {
        HookEvent.PRE_TOOL_USE,
        HookEvent.POST_TOOL_USE,
        HookEvent.POST_TOOL_USE_FAILURE,
        HookEvent.SESSION_START,
        HookEvent.USER_PROMPT_SUBMIT,
        HookEvent.SUBAGENT_START,
        HookEvent.NOTIFICATION,
        HookEvent.PERMISSION_REQUEST,
    }
    if event in events_with_hook_specific and len(hook_specific) > 1:
        result["hookSpecificOutput"] = hook_specific
    return result
