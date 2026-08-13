"""Galatea Agent runtime built on ClaudeSDKClient."""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Literal, Optional

from claude_agent_sdk import ClaudeSDKClient

from agent.core import (
    AgentSDKConfig,
    CLAUDE_CODE_BASE_ALLOWED_TOOLS,
    CLAUDE_CODE_TOOLS_PRESET,
    GalateaSDKRuntime,
    SDKRunResult,
    message_display_parts,
)
from agent.policies.permission import DEFAULT_DISALLOWED_TOOLS

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

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
GIT_CONTEXT_MAX_CHARS = 20000


def _serialize_to_oneline(obj: Any) -> str:
    """Serialize an object to one-line JSON without truncation."""
    try:
        if hasattr(obj, "__dict__"):
            serializable = {}
            for key, value in obj.__dict__.items():
                if key.startswith("_"):
                    continue
                try:
                    json.dumps(value)
                    serializable[key] = value
                except (TypeError, ValueError):
                    serializable[key] = str(value)
            obj = serializable
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str)
    except Exception as exc:  # noqa: BLE001 - logging fallback should never crash callers
        return f"<serialization_error: {exc}, repr: {repr(obj)[:1000]}>"


class GalateaRuntime:
    """
    Backwards-compatible runtime facade over the reusable GalateaSDKRuntime.

    It keeps the existing simple API while enabling SDK-native hooks,
    permissions, structured output, session store/resume, context usage checks,
    and MCP lifecycle controls.
    """

    def __init__(
        self,
        project_root: Path,
        mlflow_tracking_uri: str = "http://127.0.0.1:5000",
        model: str = "claude-opus-5",
        auto_load_config: bool = True,
        allowed_tools: Optional[list[str]] = None,
        disallowed_tools: Optional[list[str]] = None,
        tools: list[str] | dict[str, str] | None = None,
        permission_mode: str = "dontAsk",
        system_prompt: Optional[str | Dict[str, Any]] = None,
        skills: list[str] | Literal["all"] | None = None,
        output_schema: Optional[Dict[str, Any]] = None,
        max_turns: int = 12,
        max_budget_usd: float = 0.20,
    ) -> None:
        self.project_root = project_root
        self.mlflow_uri = mlflow_tracking_uri
        self.model = model
        self.output_schema = output_schema
        self.config = AgentSDKConfig(
            project_root=project_root,
            model=model,
            agent_type="runtime",
            allowed_tools=allowed_tools or _default_allowed_tools(),
            disallowed_tools=(
                list(DEFAULT_DISALLOWED_TOOLS)
                if disallowed_tools is None
                else disallowed_tools
            ),
            tools=[] if tools is None else tools,
            permission_mode=permission_mode,
            system_prompt=system_prompt,
            skills=skills,
            output_schema=output_schema,
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            auto_load_config=auto_load_config,
        )
        self.sdk_runtime = GalateaSDKRuntime(self.config)

    @property
    def client(self) -> Optional[ClaudeSDKClient]:
        """Expose the underlying SDK client for advanced callers."""
        return self.sdk_runtime._client

    async def __aenter__(self) -> "GalateaRuntime":
        await self.sdk_runtime.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.sdk_runtime.__aexit__(*args)

    async def query(
        self,
        prompt: str,
        output_schema: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[Any]:
        """
        Execute a query and stream raw SDK messages.

        If output_schema is supplied for this call, it is appended as an
        instruction for backwards compatibility. For strict SDK schema
        enforcement, construct the runtime with output_schema.
        """
        final_prompt = prompt
        if is_git_commit_push_request(prompt):
            final_prompt = build_git_commit_push_prompt(self.project_root, prompt)
        if output_schema:
            final_prompt = (
                f"{final_prompt}\n\nReturn structured JSON matching this schema:\n"
                f"{json.dumps(output_schema, ensure_ascii=False)}"
            )

        logger.info(
            "MODEL_REQUEST: %s",
            _serialize_to_oneline(
                {
                    "type": "request",
                    "model": self.model,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "prompt": final_prompt,
                    "output_schema": output_schema or self.output_schema,
                }
            ),
        )

        async for message in self.sdk_runtime.stream_query(final_prompt):
            logger.info(
                "MODEL_RESPONSE: %s",
                _serialize_to_oneline(
                    {
                        "type": "response",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "message": message_display_parts(message),
                    }
                ),
            )
            yield message

    async def run(self, prompt: str) -> SDKRunResult:
        """Execute a query and return the collected, validated result."""
        final_prompt = (
            build_git_commit_push_prompt(self.project_root, prompt)
            if is_git_commit_push_request(prompt)
            else prompt
        )
        return await self.sdk_runtime.query(final_prompt)

    async def inspect_platform(self) -> Dict[str, Any]:
        """Use the agent to inspect Galatea platform state."""
        prompt = f"""检查位于 {self.project_root} 的 Galatea ML 训练平台。

请使用可用的只读检查工具来检查：
1. 列出 train-model/ 中的所有训练项目
2. 检查关键服务的健康状况：mlflow（端口 5000）、minio（端口 9000）
3. 检查 Ray 集群状态
4. 对于 'ray-cats-and-dogs' 项目，检查其结构

在清晰的报告中总结发现，不要执行 Bash、写文件或修改任何平台状态。"""

        result = await self.run(prompt)
        return {
            "status": "success",
            "response": result.text or result.result_message.result,
            "structured_output": result.structured_output,
            "tool_calls": [call.name for call in result.tool_calls],
            "cost_usd": result.total_cost_usd,
            "tokens": result.total_tokens,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def get_context_usage(self) -> Dict[str, Any]:
        """Expose SDK context usage."""
        return await self.sdk_runtime.get_context_usage()

    async def check_context_usage(self) -> Dict[str, Any]:
        """Expose threshold-aware context usage diagnostics."""
        return await self.sdk_runtime.check_context_usage()

    async def interrupt(self) -> None:
        await self.sdk_runtime.interrupt()

    async def stop_task(self, task_id: str) -> None:
        await self.sdk_runtime.stop_task(task_id)

    async def get_mcp_status(self) -> Dict[str, Any]:
        return await self.sdk_runtime.get_mcp_status()


def _default_allowed_tools() -> list[str]:
    alias = "galatea-platform"
    return [
        f"mcp__{alias}__list_training_projects",
        f"mcp__{alias}__inspect_project_structure",
        f"mcp__{alias}__check_service_health",
        f"mcp__{alias}__inspect_mlflow_experiment",
        f"mcp__{alias}__inspect_ray_status",
    ]


def claude_code_allowed_tools() -> list[str]:
    """Return all Galatea inspection tools plus base Claude Code tools."""
    return list(
        dict.fromkeys(
            [
                *_default_allowed_tools(),
                *CLAUDE_CODE_BASE_ALLOWED_TOOLS,
            ]
        )
    )


def claude_code_tools_preset() -> dict[str, str]:
    """Return the Claude SDK preset that enables default Claude Code tools."""
    return dict(CLAUDE_CODE_TOOLS_PRESET)


def git_commit_push_allowed_tools() -> list[str]:
    """Return a narrow Claude Code-style allowlist for commit and push tasks."""
    return list(
        dict.fromkeys(
            [
                *_default_allowed_tools(),
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
    normalized = text.strip().lower()
    if normalized.startswith("## context") and "## task" in normalized:
        return False
    if normalized.startswith("/commit-push"):
        return True
    compact = normalized.replace(" ", "")
    return (
        ("commit" in normalized and "push" in normalized)
        or "提交推送" in compact
        or "提交并推送" in compact
        or "提交和推送" in compact
    )


def build_git_commit_push_prompt(project_root: Path, user_request: str) -> str:
    """Build a Claude Code-style prompt for commit and push automation."""
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
