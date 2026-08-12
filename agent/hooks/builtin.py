"""
Built-in hooks for common patterns.

Provides pre-built hooks for logging, cost tracking, and validation.
"""

from agent.hooks.types import HookInput, HookContext, HookOutput


async def logging_hook(
    input_data: HookInput,
    context: HookContext,
) -> HookOutput:
    """
    Log all tool uses and responses.

    Args:
        input_data: Hook input
        context: Hook context

    Returns:
        Hook output (pass-through)

    Raises:
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - Logging hook")


async def cost_tracking_hook(
    input_data: HookInput,
    context: HookContext,
) -> HookOutput:
    """
    Track API costs in context metadata.

    Args:
        input_data: Hook input
        context: Hook context

    Returns:
        Hook output with cost metadata

    Raises:
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - Cost tracking hook")


async def audit_hook(
    input_data: HookInput,
    context: HookContext,
) -> HookOutput:
    """
    Record audit trail for tool uses.

    Args:
        input_data: Hook input
        context: Hook context

    Returns:
        Hook output (pass-through)

    Raises:
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - Audit hook")


async def validation_hook(
    input_data: HookInput,
    context: HookContext,
) -> HookOutput:
    """
    Validate tool inputs before execution.

    Args:
        input_data: Hook input
        context: Hook context

    Returns:
        Hook output with validation result

    Raises:
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - Validation hook")
