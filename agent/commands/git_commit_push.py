"""Controlled git commit-and-push prompt command."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal, Sequence

from agent.commands.base import CommandContext, CommandInvocation, CommandPlan
from agent.commands.toolsets import default_platform_allowed_tools
from agent.core import CLAUDE_CODE_TOOLS_PRESET

GIT_CONTEXT_MAX_CHARS = 20000

CLAUDE_CODE_GIT_COMMIT_PUSH_ALLOWED_TOOLS = [
    "Bash(git add:*)",
    "Bash(git branch:*)",
    "Bash(git checkout --branch:*)",
    "Bash(git checkout -b:*)",
    "Bash(git commit:*)",
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(git push:*)",
    "Bash(git remote:*)",
    "Bash(git rev-parse:*)",
    "Bash(git status:*)",
    "Bash(git branch --show-current)",
]
CLAUDE_CODE_GIT_COMMIT_PUSH_DISALLOWED_TOOLS = [
    "Bash(git push --force*)",
    "Bash(git push * --force*)",
    "Bash(git push -f*)",
    "Bash(git push * -f*)",
]

GIT_AUTOMATION_SYSTEM_PROMPT = """You are running inside the Galatea repository.

When the user asks to commit and push code, follow this workflow without stopping
after inspection commands:
1. Inspect the current branch, status, and relevant diff.
2. Stage only relevant source changes and create a normal commit; never amend.
3. Push the current branch to its configured upstream, or to origin with
   --set-upstream if no upstream exists.

Git safety rules:
- Never run destructive git commands such as reset --hard, clean, or force-push
  unless the user explicitly asks for that exact action.
- Never skip hooks with --no-verify or similar flags unless explicitly asked.
- Do not commit secrets, datasets, checkpoints, generated models, runtime DBs,
  or platform-data artifacts.
- If authentication or network access blocks push, report the exact command and
  error instead of retrying indefinitely.
"""


def git_commit_push_allowed_tools(alias: str = "galatea-platform") -> list[str]:
    """Return a narrow Claude Code-style allowlist for commit and push tasks."""
    return list(
        dict.fromkeys(
            [
                *default_platform_allowed_tools(alias),
                "Read",
                "Glob",
                "Grep",
                "LS",
                *CLAUDE_CODE_GIT_COMMIT_PUSH_ALLOWED_TOOLS,
            ]
        )
    )


def git_commit_push_disallowed_tools() -> list[str]:
    """Return scoped git commands that remain blocked in commit/push automation."""
    return list(CLAUDE_CODE_GIT_COMMIT_PUSH_DISALLOWED_TOOLS)


def git_commit_push_system_prompt() -> str:
    """Return the system prompt fragment for controlled git commit/push automation."""
    return GIT_AUTOMATION_SYSTEM_PROMPT


def is_git_commit_push_request(text: str) -> bool:
    """Detect common English and Chinese commit-and-push requests."""
    return GitCommitPushCommand().matches_natural_language(text) or _is_git_commit_push_slash(text)


def build_git_commit_push_prompt(project_root: Path, user_request: str) -> str:
    """Build a Claude Code-style prompt for commit and push automation."""
    return _build_git_commit_push_prompt(project_root, user_request)


@dataclass(frozen=True)
class GitCommitPushCommand:
    """Prompt command matching Claude Code's command-scoped tool pattern."""

    type: Literal["prompt"] = "prompt"
    name: str = "commit-push"
    description: str = "Commit relevant changes and push the current branch"
    aliases: Sequence[str] = ("提交推送", "提交并推送", "提交和推送")
    source: Literal["builtin"] = "builtin"
    allowed_tools: Sequence[str] = ()
    disallowed_tools: Sequence[str] = ()
    system_prompt: str | dict[str, Any] | None = GIT_AUTOMATION_SYSTEM_PROMPT
    content_length: int = 0
    progress_message: str = "committing and pushing"
    user_invocable: bool = True

    _default_allowed_tools: ClassVar[tuple[str, ...]] = tuple(git_commit_push_allowed_tools())
    _default_disallowed_tools: ClassVar[tuple[str, ...]] = tuple(git_commit_push_disallowed_tools())

    def __post_init__(self) -> None:
        if not self.allowed_tools:
            object.__setattr__(self, "allowed_tools", self._default_allowed_tools)
        if not self.disallowed_tools:
            object.__setattr__(self, "disallowed_tools", self._default_disallowed_tools)

    def matches_natural_language(self, text: str) -> bool:
        normalized = text.strip().lower()
        if normalized.startswith("## context") and "## task" in normalized:
            return False
        if (
            normalized.startswith("you are running inside the galatea repository.")
            and "## git safety protocol" in normalized
            and "## task" in normalized
        ):
            return False
        if normalized.startswith("/"):
            return False
        compact = normalized.replace(" ", "")
        return (
            ("commit" in normalized and "push" in normalized)
            or "提交推送" in compact
            or "提交并推送" in compact
            or "提交和推送" in compact
        )

    def build_plan(
        self,
        invocation: CommandInvocation,
        context: CommandContext,
    ) -> CommandPlan:
        prompt = _build_git_commit_push_prompt(
            context.project_root,
            _user_request_for_invocation(invocation),
        )
        return CommandPlan(
            prompt=f"{GIT_AUTOMATION_SYSTEM_PROMPT}\n{prompt}",
            command_name=self.name,
            tools=dict(CLAUDE_CODE_TOOLS_PRESET),
            allowed_tools=tuple(self.allowed_tools),
            disallowed_tools=tuple(self.disallowed_tools),
            progress_message=self.progress_message,
            invocation=invocation,
        )


def _is_git_commit_push_slash(text: str) -> bool:
    trimmed = text.strip().lower()
    if not trimmed.startswith("/"):
        return False
    command_name = trimmed[1:].split(maxsplit=1)[0]
    return command_name in {"commit-push", "提交推送", "提交并推送", "提交和推送"}


def _user_request_for_invocation(invocation: CommandInvocation) -> str:
    if invocation.trigger == "slash" and invocation.args:
        return invocation.args
    return invocation.raw_input


def _build_git_commit_push_prompt(project_root: Path, user_request: str) -> str:
    context_commands = {
        "git status --branch --short": _run_git_context_command(
            project_root,
            ["git", "status", "--branch", "--short"],
        ),
        "git diff HEAD": _run_git_context_command(
            project_root,
            ["git", "diff", "HEAD"],
            max_chars=GIT_CONTEXT_MAX_CHARS,
        ),
        "git branch --show-current": _run_git_context_command(
            project_root,
            ["git", "branch", "--show-current"],
        ),
        "git log --oneline -10": _run_git_context_command(
            project_root,
            ["git", "log", "--oneline", "-10"],
        ),
        "git remote -v": _run_git_context_command(
            project_root,
            ["git", "remote", "-v"],
        ),
    }
    context = "\n".join(
        f"- `{command}`:\n```text\n{output or '(no output)'}\n```"
        for command, output in context_commands.items()
    )
    extra = user_request.removeprefix("/commit-push").strip()
    if not extra:
        extra = user_request

    return f"""## Context

{context}

## Git Safety Protocol

- Never update git config.
- Never amend commits unless the user explicitly asks for amend.
- Never skip hooks with --no-verify, --no-gpg-sign, or similar flags unless explicitly requested.
- Never run destructive commands such as reset --hard, clean, or force-push unless explicitly requested.
- Do not commit secrets, datasets, checkpoints, generated models, runtime DBs, or platform-data artifacts.
- If there are no changes to commit, report that and do not create an empty commit.

## Task

The user's request was:
{extra}

Based on the context above, continue all the way through the workflow:
1. Stage relevant source changes.
2. Create one normal commit with a concise imperative message.
3. Push the current branch. If it has no upstream, push with --set-upstream origin <branch>.

Do not stop after status or diff inspection. You have the capability to call
multiple tools in one response; use that to stage, commit, and push. Return the
commit hash and push result when finished."""


def _run_git_context_command(
    project_root: Path,
    command: list[str],
    *,
    max_chars: int = 6000,
) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=project_root,
            check=False,
            text=True,
            capture_output=True,
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001 - context collection should not block the agent
        return f"[context command failed: {exc}]"

    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
    output = output.strip()
    if completed.returncode != 0:
        output = f"[exit {completed.returncode}]\n{output}".strip()
    if len(output) > max_chars:
        return output[:max_chars] + "\n...[truncated]..."
    return output
