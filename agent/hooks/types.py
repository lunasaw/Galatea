"""
Hook type definitions for Galatea agents.

Defines hook events, contexts, and callback signatures.
Reference: Claude SDK's HookContext, HookInput, HookJSONOutput.
"""

from typing import Dict, Any, Optional, Callable, Awaitable, List
from dataclasses import dataclass
from enum import Enum


class HookEvent(str, Enum):
    """
    Hook event types.

    Reference: Claude SDK hook events.
    """

    SESSION_START = "SessionStart"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    RESULT_COMPLETE = "ResultComplete"


@dataclass
class HookContext:
    """
    Context passed to hook callbacks.

    Contains session state, agent info, and accumulated metadata.
    """

    session_id: str
    agent_type: str
    project_name: Optional[str] = None
    turn_number: int = 0
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        """Initialize default metadata."""
        if self.metadata is None:
            self.metadata = {}


@dataclass
class HookInput:
    """
    Input data for hook callbacks.

    Content varies by hook event type.
    """

    event: HookEvent
    data: Dict[str, Any]

    # Tool-specific fields (for PreToolUse/PostToolUse)
    tool_name: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    tool_response: Optional[Any] = None
    tool_use_id: Optional[str] = None


@dataclass
class HookOutput:
    """
    Output from hook callbacks.

    Can modify behavior, inject messages, or deny permissions.
    Reference: Claude SDK's HookJSONOutput.
    """

    # Permission control
    permission_decision: Optional[str] = None  # "allow", "deny"
    permission_decision_reason: Optional[str] = None

    # Execution control
    continue_: bool = True
    stop_reason: Optional[str] = None

    # Message injection
    system_message: Optional[str] = None
    additional_context: Optional[str] = None

    # Metadata
    reason: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result: Dict[str, Any] = {}

        if self.permission_decision is not None:
            result["permissionDecision"] = self.permission_decision
        if self.permission_decision_reason is not None:
            result["permissionDecisionReason"] = self.permission_decision_reason

        result["continue"] = self.continue_
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

        return result


# Hook callback signature
HookCallback = Callable[[HookInput, HookContext], Awaitable[HookOutput]]


@dataclass
class HookMatcher:
    """
    Hook matcher for filtering hook invocations.

    Reference: Claude SDK's HookMatcher pattern.
    """

    matcher: Optional[str]  # Tool name pattern or None for all
    hooks: List[HookCallback]


class HookRegistry:
    """
    Registry for hook callbacks.

    Manages hook registration, matching, and invocation.
    """

    def __init__(self):
        """Initialize empty registry."""
        self._hooks: Dict[HookEvent, List[HookMatcher]] = {
            event: [] for event in HookEvent
        }

    def register(
        self,
        event: HookEvent,
        matcher: HookMatcher,
    ) -> None:
        """
        Register hook callback.

        Args:
            event: Hook event type
            matcher: Hook matcher with callbacks

        Raises:
            NotImplementedError: Future: Stage 2+
        """
        raise NotImplementedError("Future: Stage 2+ - Hook registration")

    def unregister(
        self,
        event: HookEvent,
        callback: HookCallback,
    ) -> None:
        """
        Unregister hook callback.

        Args:
            event: Hook event type
            callback: Callback to remove

        Raises:
            NotImplementedError: Future: Stage 2+
        """
        raise NotImplementedError("Future: Stage 2+ - Hook unregistration")

    async def invoke(
        self,
        event: HookEvent,
        input_data: HookInput,
        context: HookContext,
    ) -> List[HookOutput]:
        """
        Invoke all matching hooks for event.

        Args:
            event: Hook event type
            input_data: Hook input data
            context: Hook context

        Returns:
            List of hook outputs

        Raises:
            NotImplementedError: Future: Stage 2+
        """
        raise NotImplementedError("Future: Stage 2+ - Hook invocation")

    def get_hooks(self, event: HookEvent) -> List[HookMatcher]:
        """
        Get registered hooks for event.

        Args:
            event: Hook event type

        Returns:
            List of hook matchers

        Raises:
            NotImplementedError: Future: Stage 2+
        """
        raise NotImplementedError("Future: Stage 2+ - Hook retrieval")
