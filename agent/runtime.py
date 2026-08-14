"""Galatea Agent runtime built on ClaudeSDKClient."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Literal, Optional

from claude_agent_sdk import ClaudeSDKClient

from agent.commands import (
    CLAUDE_CODE_GIT_COMMIT_PUSH_ALLOWED_TOOLS,
    CLAUDE_CODE_GIT_COMMIT_PUSH_DISALLOWED_TOOLS,
    GIT_AUTOMATION_SYSTEM_PROMPT,
    CommandContext,
    CommandInvocation,
    CommandPlan,
    CommandRegistry,
    build_git_commit_push_prompt,
    claude_code_allowed_tools as _claude_code_allowed_tools,
    default_command_registry,
    default_platform_allowed_tools,
    git_commit_push_allowed_tools,
    git_commit_push_disallowed_tools,
    git_commit_push_system_prompt,
    is_git_commit_push_request,
)
from agent.core import (
    AgentSDKConfig,
    CLAUDE_CODE_TOOLS_PRESET,
    GalateaSDKRuntime,
    SDKRunResult,
    message_display_parts,
)
from agent.policies.permission import DEFAULT_DISALLOWED_TOOLS

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


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
        agents: Optional[Dict[str, Any]] = None,
        task_budget_tokens: Optional[int] = None,
        include_hook_events: bool = True,
        command_registry: CommandRegistry | None = None,
    ) -> None:
        self.project_root = project_root
        self.mlflow_uri = mlflow_tracking_uri
        self.model = model
        self.output_schema = output_schema
        self.command_registry = command_registry or default_command_registry()
        self._explicit_disallowed_tools = disallowed_tools is not None
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
            agents=agents,
            include_hook_events=include_hook_events,
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            task_budget_tokens=task_budget_tokens,
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
        plan = self.build_command_plan(prompt)
        final_prompt = plan.prompt
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
                    "command": plan.command_name,
                    "output_schema": output_schema or self.output_schema,
                }
            ),
        )

        async with self._runtime_for_plan(plan) as runtime:
            async for message in runtime.stream_query(final_prompt):
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

    async def stream_query(
        self,
        prompt: str,
        output_schema: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[Any]:
        """Alias for query() to match GalateaSDKRuntime's streaming API."""
        async for message in self.query(prompt, output_schema=output_schema):
            yield message

    async def run(self, prompt: str) -> SDKRunResult:
        """Execute a query and return the collected, validated result."""
        plan = self.build_command_plan(prompt)
        async with self._runtime_for_plan(plan) as runtime:
            return await runtime.query(plan.prompt)

    def build_command_plan(self, prompt: str) -> CommandPlan:
        """Resolve slash/natural-language commands into an SDK query plan."""
        return self.command_registry.build_plan(prompt, self._command_context())

    def _command_context(self) -> CommandContext:
        return CommandContext(
            project_root=self.project_root,
            mlflow_tracking_uri=self.mlflow_uri,
            model=self.model,
        )

    def _runtime_for_plan(self, plan: CommandPlan) -> "_PlannedRuntime":
        return _PlannedRuntime(self.sdk_runtime, self._build_runtime_for_plan(plan))

    def _build_runtime_for_plan(self, plan: CommandPlan) -> GalateaSDKRuntime | None:
        if not plan.is_command or self._base_runtime_covers_plan(plan):
            return None

        disallowed_tools = list(plan.disallowed_tools) or self.config.disallowed_tools
        if self._explicit_disallowed_tools and plan.disallowed_tools:
            disallowed_tools = list(dict.fromkeys([*self.config.disallowed_tools, *plan.disallowed_tools]))

        command_config = replace(
            self.config,
            agent_type=f"runtime-command-{plan.command_name}",
            stage_run_id=f"{self.sdk_runtime.stage_run_id}:{plan.command_name}",
            model=plan.model or self.config.model,
            tools=self.config.tools if plan.tools is None else plan.tools,
            allowed_tools=list(plan.allowed_tools) or self.config.allowed_tools,
            disallowed_tools=disallowed_tools,
            system_prompt=_merge_system_prompt(self.config.system_prompt, plan.system_prompt),
            max_turns=plan.max_turns or self.config.max_turns,
        )
        return GalateaSDKRuntime(command_config)

    def _base_runtime_covers_plan(self, plan: CommandPlan) -> bool:
        if not plan.is_command:
            return True
        if plan.tools is not None and plan.tools != self.config.tools:
            return False
        if plan.allowed_tools and not set(plan.allowed_tools).issubset(self.config.allowed_tools):
            return False
        if plan.disallowed_tools and set(plan.disallowed_tools) != set(self.config.disallowed_tools):
            return False
        if not _system_prompt_contains(self.config.system_prompt, plan.system_prompt):
            return False
        return True

    async def inspect_platform(self, detailed: bool = False) -> Dict[str, Any]:
        """Use the agent to inspect Galatea platform state."""
        prompt = self._build_platform_inspection_prompt(detailed=detailed)

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

    def _build_platform_inspection_prompt(self, detailed: bool = False) -> str:
        """Build the stable platform inspection prompt used by the CLI and API."""
        if detailed:
            return f"""检查位于 {self.project_root} 的 Galatea ML 训练平台，并输出详细审计报告。

请使用可用的只读检查工具来检查：
1. 列出 train-model/ 中的所有训练项目
2. 检查关键服务的健康状况：mlflow（端口 5000）、minio（端口 9000）
3. 检查 Ray 集群状态
4. 对于每个训练项目，检查目录结构、配置文件和测试覆盖
5. 标记任何与 SDK/Claude 基座、权限边界、依赖声明或旧入口有关的风险

报告必须明确区分事实、风险和建议，不要执行 Bash、写文件或修改任何平台状态。"""

        return f"""检查位于 {self.project_root} 的 Galatea ML 训练平台。

请使用可用的只读检查工具来检查：
1. 列出 train-model/ 中的所有训练项目
2. 检查关键服务的健康状况：mlflow（端口 5000）、minio（端口 9000）
3. 检查 Ray 集群状态
4. 对于 'ray-cats-and-dogs' 项目，检查其结构

在清晰的报告中总结发现，不要执行 Bash、写文件或修改任何平台状态。"""

    async def get_context_usage(self) -> Dict[str, Any]:
        """Expose SDK context usage."""
        return await self.sdk_runtime.get_context_usage()

    async def check_context_usage(self) -> Dict[str, Any]:
        """Expose threshold-aware context usage diagnostics."""
        return await self.sdk_runtime.check_context_usage()

    def context_compaction_instructions(self) -> str:
        """Expose SDK context compaction guidance."""
        return self.sdk_runtime.context_compaction_instructions()

    async def interrupt(self) -> None:
        await self.sdk_runtime.interrupt()

    async def stop_task(self, task_id: str) -> None:
        await self.sdk_runtime.stop_task(task_id)

    async def get_mcp_status(self) -> Dict[str, Any]:
        return await self.sdk_runtime.get_mcp_status()


class _PlannedRuntime:
    """Context manager for command-specific SDK runtimes."""

    def __init__(
        self,
        base_runtime: GalateaSDKRuntime,
        command_runtime: GalateaSDKRuntime | None,
    ) -> None:
        self.base_runtime = base_runtime
        self.command_runtime = command_runtime

    async def __aenter__(self) -> GalateaSDKRuntime:
        if self.command_runtime is None:
            return self.base_runtime
        await self.command_runtime.__aenter__()
        return self.command_runtime

    async def __aexit__(self, *args: Any) -> None:
        if self.command_runtime is not None:
            await self.command_runtime.__aexit__(*args)


def _default_allowed_tools() -> list[str]:
    return default_platform_allowed_tools()


def claude_code_allowed_tools() -> list[str]:
    """Return all Galatea inspection tools plus base Claude Code tools."""
    return _claude_code_allowed_tools()


def claude_code_tools_preset() -> dict[str, str]:
    """Return the Claude SDK preset that enables default Claude Code tools."""
    return dict(CLAUDE_CODE_TOOLS_PRESET)


def _merge_system_prompt(
    base: str | Dict[str, Any] | None,
    scoped: str | Dict[str, Any] | None,
) -> str | Dict[str, Any] | None:
    if base is None:
        return scoped
    if scoped is None:
        return base
    if isinstance(base, str) and isinstance(scoped, str):
        if scoped in base:
            return base
        return f"{base.rstrip()}\n\n{scoped.lstrip()}"
    return scoped


def _system_prompt_contains(
    base: str | Dict[str, Any] | None,
    scoped: str | Dict[str, Any] | None,
) -> bool:
    if scoped is None:
        return True
    if base is None:
        return False
    if base == scoped:
        return True
    if isinstance(base, str) and isinstance(scoped, str):
        return scoped in base
    return False
