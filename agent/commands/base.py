"""Claude Code-style command abstractions for Galatea agent turns."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, Sequence, runtime_checkable

CommandType = Literal["prompt"]
CommandSource = Literal["builtin", "project", "plugin", "mcp", "skill"]
InvocationTrigger = Literal["slash", "natural"]


@dataclass(frozen=True)
class CommandContext:
    """Runtime context available while expanding a command into a model prompt."""

    project_root: Path
    mlflow_tracking_uri: str | None = None
    model: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CommandInvocation:
    """Parsed command invocation, mirroring Claude Code slash command parsing."""

    name: str
    args: str
    raw_input: str
    trigger: InvocationTrigger


@dataclass(frozen=True)
class CommandPlan:
    """Concrete query plan produced by a command before SDK execution."""

    prompt: str
    command_name: str | None = None
    tools: list[str] | dict[str, str] | None = None
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    system_prompt: str | dict[str, Any] | None = None
    model: str | None = None
    max_turns: int | None = None
    progress_message: str | None = None
    invocation: CommandInvocation | None = None

    @property
    def is_command(self) -> bool:
        return self.command_name is not None


@dataclass(frozen=True)
class ParsedSlashCommand:
    """A parsed slash command name and argument tail."""

    command_name: str
    args: str


@runtime_checkable
class PromptCommand(Protocol):
    """Prompt-expansion command, equivalent to Claude Code's prompt commands."""

    type: CommandType
    name: str
    description: str
    aliases: Sequence[str]
    source: CommandSource
    allowed_tools: Sequence[str]
    disallowed_tools: Sequence[str]
    system_prompt: str | dict[str, Any] | None
    content_length: int
    progress_message: str
    user_invocable: bool

    def matches_natural_language(self, text: str) -> bool:
        """Return True when non-slash text should invoke this command."""
        ...

    def build_plan(
        self,
        invocation: CommandInvocation,
        context: CommandContext,
    ) -> CommandPlan:
        """Expand the invocation into a prompt and scoped execution metadata."""
        ...
