"""SDK-native hook types plus Galatea-owned audit context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, get_args

from claude_agent_sdk import (
    HookCallback,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
)
from claude_agent_sdk.types import HookEvent


SDK_HOOK_EVENTS = frozenset(
    value
    for event_literal in get_args(HookEvent)
    for value in get_args(event_literal)
)


@dataclass
class GalateaHookContext:
    """Mutable Galatea audit state captured by SDK hook factories."""

    session_id: str
    agent_type: str
    project_name: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "GalateaHookContext",
    "HookCallback",
    "HookContext",
    "HookEvent",
    "HookInput",
    "HookJSONOutput",
    "HookMatcher",
    "SDK_HOOK_EVENTS",
]
