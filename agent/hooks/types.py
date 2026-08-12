"""Hook type definitions and local registry for Galatea agents."""

from __future__ import annotations

import fnmatch
import inspect
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional


class HookEvent(str, Enum):
    """Hook event types supported by Galatea and the Claude SDK."""

    SESSION_START = "SessionStart"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    RESULT_COMPLETE = "ResultComplete"
    STOP = "Stop"
    PRE_COMPACT = "PreCompact"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    NOTIFICATION = "Notification"
    PERMISSION_REQUEST = "PermissionRequest"


@dataclass
class HookContext:
    """Context passed to local hook callbacks."""

    session_id: str
    agent_type: str
    project_name: Optional[str] = None
    turn_number: int = 0
    metadata: Dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}


@dataclass
class HookInput:
    """Input data for local hook callbacks."""

    event: HookEvent
    data: Dict[str, Any]
    tool_name: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    tool_response: Optional[Any] = None
    tool_use_id: Optional[str] = None


@dataclass
class HookOutput:
    """Output from local hooks and a helper for SDK hook JSON."""

    permission_decision: Optional[str] = None
    permission_decision_reason: Optional[str] = None
    continue_: bool = True
    stop_reason: Optional[str] = None
    system_message: Optional[str] = None
    additional_context: Optional[str] = None
    reason: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    updated_tool_output: Optional[Any] = None
    updated_mcp_tool_output: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a compact dictionary for local tests and audit logs."""
        result: Dict[str, Any] = {"continue": self.continue_}
        if self.permission_decision is not None:
            result["permissionDecision"] = self.permission_decision
        if self.permission_decision_reason is not None:
            result["permissionDecisionReason"] = self.permission_decision_reason
        if self.stop_reason is not None:
            result["stopReason"] = self.stop_reason
        if self.system_message is not None:
            result["systemMessage"] = self.system_message
        if self.additional_context is not None:
            result["additionalContext"] = self.additional_context
        if self.reason is not None:
            result["reason"] = self.reason
        if self.metadata is not None:
            result["metadata"] = self.metadata
        if self.updated_tool_output is not None:
            result["updatedToolOutput"] = self.updated_tool_output
        if self.updated_mcp_tool_output is not None:
            result["updatedMCPToolOutput"] = self.updated_mcp_tool_output
        return result


HookCallback = Callable[[HookInput, HookContext], Awaitable[HookOutput] | HookOutput]


@dataclass
class HookMatcher:
    """Hook matcher for filtering hook invocations."""

    matcher: Optional[str]
    hooks: List[HookCallback]
    timeout: Optional[float] = None


class HookRegistry:
    """Registry for local hook callbacks."""

    def __init__(self) -> None:
        self._hooks: Dict[HookEvent, List[HookMatcher]] = {
            event: [] for event in HookEvent
        }

    def register(self, event: HookEvent, matcher: HookMatcher) -> None:
        """Register hook callbacks for an event."""
        self._hooks.setdefault(event, []).append(matcher)

    def unregister(self, event: HookEvent, callback: HookCallback) -> None:
        """Unregister a callback from an event."""
        remaining = []
        for matcher in self._hooks.get(event, []):
            hooks = [hook for hook in matcher.hooks if hook is not callback]
            if hooks:
                remaining.append(
                    HookMatcher(
                        matcher=matcher.matcher,
                        hooks=hooks,
                        timeout=matcher.timeout,
                    )
                )
        self._hooks[event] = remaining

    async def invoke(
        self,
        event: HookEvent,
        input_data: HookInput,
        context: HookContext,
    ) -> List[HookOutput]:
        """Invoke matching hooks for an event."""
        outputs: List[HookOutput] = []
        for matcher in self._hooks.get(event, []):
            if not _matches(matcher.matcher, input_data.tool_name):
                continue
            for callback in matcher.hooks:
                output = callback(input_data, context)
                if inspect.isawaitable(output):
                    output = await output
                outputs.append(output)
        return outputs

    def get_hooks(self, event: HookEvent) -> List[HookMatcher]:
        """Get registered matchers for an event."""
        return list(self._hooks.get(event, []))

    def clear(self, event: Optional[HookEvent] = None) -> None:
        """Clear one event or all events."""
        if event is None:
            self._hooks = {hook_event: [] for hook_event in HookEvent}
        else:
            self._hooks[event] = []


def _matches(matcher: Optional[str], tool_name: Optional[str]) -> bool:
    if matcher is None:
        return True
    if tool_name is None:
        return False
    for part in matcher.split("|"):
        pattern = part.strip()
        if pattern and (pattern == tool_name or fnmatch.fnmatchcase(tool_name, pattern)):
            return True
    return False
