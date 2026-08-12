"""
Hooks system for Galatea agents.

Provides hook registration, invocation, and built-in hooks.

Key components:
- HookEvent: Hook event types (PreToolUse, PostToolUse, etc.)
- HookCallback: Hook callback signature
- HookRegistry: Hook registration and management
- HookManager: High-level hook API
- Built-in hooks: Logging, cost tracking, audit, validation

Reference: Claude SDK's hooks system.
"""

from agent.hooks.types import (
    HookEvent,
    HookContext,
    HookInput,
    HookOutput,
    HookCallback,
    HookMatcher,
    HookRegistry,
)
from agent.hooks.registry import HookManager
from agent.hooks.builtin import (
    logging_hook,
    cost_tracking_hook,
    audit_hook,
    validation_hook,
    make_permission_hook,
    deny_builtin_mutation_hook,
    summarize_large_tool_output_hook,
    classify_tool_failure_hook,
    compact_context_hook,
)

__all__ = [
    "HookEvent",
    "HookContext",
    "HookInput",
    "HookOutput",
    "HookCallback",
    "HookMatcher",
    "HookRegistry",
    "HookManager",
    "logging_hook",
    "cost_tracking_hook",
    "audit_hook",
    "validation_hook",
    "make_permission_hook",
    "deny_builtin_mutation_hook",
    "summarize_large_tool_output_hook",
    "classify_tool_failure_hook",
    "compact_context_hook",
]
