"""Command registry and slash-command routing."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from agent.commands.base import (
    CommandContext,
    CommandInvocation,
    CommandPlan,
    ParsedSlashCommand,
    PromptCommand,
)


class CommandRegistry:
    """
    Registry for prompt commands.

    The shape mirrors Claude Code's command registry: command modules provide
    metadata and prompt expansion, while the registry handles lookup, aliases,
    slash parsing, and command-scoped allowed tools.
    """

    def __init__(self, commands: Iterable[PromptCommand] = ()) -> None:
        self._commands: list[PromptCommand] = []
        for command in commands:
            self.register(command)

    @property
    def commands(self) -> tuple[PromptCommand, ...]:
        return tuple(self._commands)

    def register(self, command: PromptCommand) -> None:
        existing = self.find(command.name)
        if existing is not None:
            raise ValueError(f"Command already registered: {command.name}")
        for alias in command.aliases:
            if self.find(alias) is not None:
                raise ValueError(f"Command alias already registered: {alias}")
        self._commands.append(command)

    def find(self, command_name: str) -> PromptCommand | None:
        normalized = command_name.strip().lstrip("/")
        for command in self._commands:
            if command.name == normalized or normalized in command.aliases:
                return command
        return None

    def has(self, command_name: str) -> bool:
        return self.find(command_name) is not None

    def parse_slash_command(self, text: str) -> ParsedSlashCommand | None:
        trimmed = text.strip()
        if not trimmed.startswith("/"):
            return None
        without_slash = trimmed[1:]
        if not without_slash:
            return None
        parts = without_slash.split(maxsplit=1)
        command_name = parts[0]
        args = parts[1].strip() if len(parts) > 1 else ""
        return ParsedSlashCommand(command_name=command_name, args=args)

    def resolve_invocation(self, text: str) -> CommandInvocation | None:
        parsed = self.parse_slash_command(text)
        if parsed is not None:
            command = self.find(parsed.command_name)
            if command is None or not command.user_invocable:
                return None
            return CommandInvocation(
                name=command.name,
                args=parsed.args,
                raw_input=text,
                trigger="slash",
            )

        for command in self._commands:
            if command.matches_natural_language(text):
                return CommandInvocation(
                    name=command.name,
                    args=text.strip(),
                    raw_input=text,
                    trigger="natural",
                )
        return None

    def build_plan(self, text: str, context: CommandContext) -> CommandPlan:
        invocation = self.resolve_invocation(text)
        if invocation is None:
            return CommandPlan(prompt=text)
        command = self.find(invocation.name)
        if command is None:
            return CommandPlan(prompt=text)
        plan = command.build_plan(invocation, context)
        if plan.invocation is None:
            plan = replace(plan, invocation=invocation)
        if plan.command_name is None:
            return replace(plan, command_name=command.name)
        return plan

    def allowed_tools(self) -> list[str]:
        """Return de-duplicated command-scoped allowed tools."""
        tools: list[str] = []
        for command in self._commands:
            tools.extend(command.allowed_tools)
        return list(dict.fromkeys(tools))

    def disallowed_tools(self) -> list[str]:
        """Return de-duplicated command-scoped deny rules."""
        tools: list[str] = []
        for command in self._commands:
            tools.extend(command.disallowed_tools)
        return list(dict.fromkeys(tools))

    def system_prompts(self) -> tuple[str | dict, ...]:
        """Return command system prompt fragments in registration order."""
        prompts: list[str | dict] = []
        for command in self._commands:
            if command.system_prompt is not None:
                prompts.append(command.system_prompt)
        return tuple(prompts)

    def system_prompt(self) -> str | dict | None:
        """Return an aggregate system prompt for CLIs with static SDK options."""
        prompts = self.system_prompts()
        if not prompts:
            return None
        if len(prompts) == 1:
            return prompts[0]
        if all(isinstance(prompt, str) for prompt in prompts):
            return "\n\n".join(str(prompt).strip() for prompt in prompts if str(prompt).strip())
        return prompts[-1]
