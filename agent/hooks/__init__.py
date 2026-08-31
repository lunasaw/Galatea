"""Claude SDK-native hooks used by the Galatea runtime."""

from agent.hooks.builtin import (
    classify_tool_failure_hook,
    compact_context_hook,
    make_audit_hook,
    make_logging_hook,
    make_permission_hook,
    make_permission_request_audit_hook,
    make_summarize_large_tool_output_hook,
    validation_hook,
)
from agent.hooks.registry import HookManager
from agent.hooks.types import (
    GalateaHookContext,
    HookCallback,
    HookContext,
    HookEvent,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    SDK_HOOK_EVENTS,
)

__all__ = [
    "GalateaHookContext",
    "HookCallback",
    "HookContext",
    "HookEvent",
    "HookInput",
    "HookJSONOutput",
    "HookManager",
    "HookMatcher",
    "SDK_HOOK_EVENTS",
    "classify_tool_failure_hook",
    "compact_context_hook",
    "make_audit_hook",
    "make_logging_hook",
    "make_permission_hook",
    "make_permission_request_audit_hook",
    "make_summarize_large_tool_output_hook",
    "validation_hook",
]
