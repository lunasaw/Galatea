"""Galatea command registry and built-in prompt commands."""

from agent.commands.base import (
    CommandContext,
    CommandInvocation,
    CommandPlan,
    ParsedSlashCommand,
    PromptCommand,
)
from agent.commands.git_commit_push import (
    CLAUDE_CODE_GIT_COMMIT_PUSH_ALLOWED_TOOLS,
    CLAUDE_CODE_GIT_COMMIT_PUSH_DISALLOWED_TOOLS,
    GIT_AUTOMATION_SYSTEM_PROMPT,
    GitCommitPushCommand,
    build_git_commit_push_prompt,
    git_commit_push_allowed_tools,
    git_commit_push_disallowed_tools,
    git_commit_push_system_prompt,
    is_git_commit_push_request,
)
from agent.commands.registry import CommandRegistry
from agent.commands.toolsets import (
    CLAUDE_CODE_READ_ONLY_TOOLS,
    claude_code_allowed_tools,
    claude_code_read_only_allowed_tools,
    default_platform_allowed_tools,
)


def default_command_registry() -> CommandRegistry:
    """Return the built-in command registry."""
    return CommandRegistry([GitCommitPushCommand()])


__all__ = [
    "CLAUDE_CODE_GIT_COMMIT_PUSH_ALLOWED_TOOLS",
    "CLAUDE_CODE_GIT_COMMIT_PUSH_DISALLOWED_TOOLS",
    "CLAUDE_CODE_READ_ONLY_TOOLS",
    "GIT_AUTOMATION_SYSTEM_PROMPT",
    "CommandContext",
    "CommandInvocation",
    "CommandPlan",
    "CommandRegistry",
    "GitCommitPushCommand",
    "ParsedSlashCommand",
    "PromptCommand",
    "build_git_commit_push_prompt",
    "claude_code_allowed_tools",
    "claude_code_read_only_allowed_tools",
    "default_command_registry",
    "default_platform_allowed_tools",
    "git_commit_push_allowed_tools",
    "git_commit_push_disallowed_tools",
    "git_commit_push_system_prompt",
    "is_git_commit_push_request",
]
