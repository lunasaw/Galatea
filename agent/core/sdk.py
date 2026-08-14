"""Reusable Claude SDK foundation for Galatea agents."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Literal, Optional

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookEventMessage,
    InMemorySessionStore,
    McpSdkServerConfig,
    ResultMessage,
    SessionStore as SDKSessionStore,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from agent.config import apply_anthropic_config_to_env
from agent.hooks import (
    HookContext,
    HookEvent,
    HookManager,
    audit_hook,
    classify_tool_failure_hook,
    compact_context_hook,
    deny_builtin_mutation_hook,
    logging_hook,
    make_permission_hook,
    summarize_large_tool_output_hook,
    validation_hook,
)
from agent.policies import BudgetPolicy, PermissionPolicy
from agent.policies.permission import DEFAULT_DISALLOWED_TOOLS
from agent.skills import SkillRuntimeConfig, resolve_skill_runtime
from agent.tools.server import create_galatea_mcp_server

logger = logging.getLogger(__name__)

DEFAULT_MCP_SERVER_ALIAS = "galatea-platform"
DEFAULT_MODEL = "claude-opus-5"
CLAUDE_CODE_TOOLS_PRESET = {"type": "preset", "preset": "claude_code"}
CLAUDE_CODE_BASE_ALLOWED_TOOLS = [
    "Task",
    "Agent",
    "Bash",
    "BashOutput",
    "KillBash",
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "Glob",
    "Grep",
    "LS",
    "TodoWrite",
    "NotebookRead",
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
    "ListMcpResources",
    "ReadMcpResource",
    "ReadMcpResourceDir",
    "RefreshMcpTools",
]


@dataclass
class ContextCompressionConfig:
    """Context hygiene settings for the SDK runtime."""

    max_tool_output_chars: int = 6000
    warn_at_percent: float = 70.0
    compact_at_percent: float = 85.0
    preserve_keys: List[str] = field(
        default_factory=lambda: [
            "stage_run_id",
            "ray_job_id",
            "submission_id",
            "mlflow_run_id",
            "artifact_uri",
            "artifact_digest",
            "manifest_digest",
            "objective_metric",
            "objective_mode",
            "permission_denials",
            "approval_request_id",
        ]
    )


@dataclass
class AgentSDKConfig:
    """Configuration for GalateaSDKRuntime."""

    project_root: Path
    model: str = DEFAULT_MODEL
    mcp_server_alias: str = DEFAULT_MCP_SERVER_ALIAS
    mcp_server: Optional[McpSdkServerConfig] = None
    stage_run_id: Optional[str] = None
    agent_type: str = "general"
    project_name: Optional[str] = None
    allowed_tools: List[str] = field(default_factory=list)
    disallowed_tools: List[str] = field(default_factory=lambda: list(DEFAULT_DISALLOWED_TOOLS))
    tools: list[str] | dict[str, str] | None = field(default_factory=list)
    permission_mode: str = "dontAsk"
    max_turns: int = 12
    max_budget_usd: float = 0.20
    max_tokens: Optional[int] = None
    task_budget_tokens: Optional[int] = None
    output_schema: Optional[Dict[str, Any]] = None
    system_prompt: Optional[str | Dict[str, Any]] = None
    agents: Optional[Dict[str, AgentDefinition]] = None
    include_hook_events: bool = True
    strict_mcp_config: bool = True
    setting_sources: Optional[List[str]] = field(default_factory=list)
    skills: list[str] | Literal["all"] | None = None
    skill_plugins: List[Dict[str, str]] = field(default_factory=list)
    include_legacy_codex_skills: bool = True
    include_skill_plugins: bool = True
    sync_legacy_codex_skills: bool = False
    session_store: SDKSessionStore | None = None
    session_store_flush: str = "batched"
    resume: Optional[str] = None
    fork_session: bool = False
    context: ContextCompressionConfig = field(default_factory=ContextCompressionConfig)
    auto_load_config: bool = True

    def effective_stage_run_id(self) -> str:
        if self.stage_run_id:
            return self.stage_run_id
        return f"{self.agent_type}-{uuid.uuid4()}"


@dataclass
class ToolCallRecord:
    """Audit record for one tool call observed in the message stream."""

    tool_use_id: str
    name: str
    input: Dict[str, Any]
    result: Any = None
    is_error: bool | None = None


@dataclass
class SDKRunResult:
    """Collected result for one SDK response."""

    messages: List[Any]
    result_message: ResultMessage
    text: str
    structured_output: Any = None
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    hook_events: List[HookEventMessage] = field(default_factory=list)
    context_usage: Optional[Dict[str, Any]] = None

    @property
    def total_cost_usd(self) -> float:
        return float(self.result_message.total_cost_usd or 0.0)

    @property
    def total_tokens(self) -> int:
        usage = self.result_message.usage or {}
        return int(usage.get("input_tokens", 0) + usage.get("output_tokens", 0))


class SDKRunValidationError(RuntimeError):
    """Raised when an SDK run violates Galatea runtime invariants."""


def _validate_session_store(session_store: SDKSessionStore | None) -> None:
    """Reject application state stores that do not implement the SDK protocol."""
    if session_store is None:
        return
    append = getattr(session_store, "append", None)
    load = getattr(session_store, "load", None)
    if callable(append) and callable(load):
        return
    raise TypeError(
        "GalateaSDKRuntime.session_store must implement the Claude SDK SessionStore "
        "protocol with callable append/load methods."
    )


class GalateaSDKRuntime:
    """Reusable wrapper around ClaudeSDKClient with Galatea safety defaults."""

    def __init__(
        self,
        config: AgentSDKConfig,
        hook_manager: Optional[HookManager] = None,
        permission_policy: Optional[PermissionPolicy] = None,
    ) -> None:
        self.config = config
        self.stage_run_id = config.effective_stage_run_id()
        self.mcp_server = config.mcp_server or create_galatea_mcp_server()
        _validate_session_store(config.session_store)
        self.skill_runtime = self._resolve_skill_runtime()
        self.permission_policy = permission_policy or PermissionPolicy.for_galatea(
            allowed_tools=self._policy_allowed_tools(),
            disallowed_tools=config.disallowed_tools,
            mode=config.permission_mode,
            default_behavior="deny",
        )
        self.hook_context = HookContext(
            session_id=self.stage_run_id,
            agent_type=config.agent_type,
            project_name=config.project_name,
            metadata={"stage_run_id": self.stage_run_id},
        )
        self.hook_manager = hook_manager or self._create_default_hooks()
        self.budget = BudgetPolicy(
            max_budget_usd=config.max_budget_usd,
            max_tokens=config.max_tokens,
        )
        self._client: ClaudeSDKClient | None = None

        if config.auto_load_config:
            apply_anthropic_config_to_env()

    async def __aenter__(self) -> "GalateaSDKRuntime":
        self._client = ClaudeSDKClient(options=self.build_options())
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client is not None:
            await self._client.__aexit__(*args)
            self._client = None

    def build_options(self) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions using SDK-native features."""
        output_format = None
        if self.config.output_schema is not None:
            output_format = {"type": "json_schema", "schema": self.config.output_schema}

        task_budget = None
        if self.config.task_budget_tokens is not None:
            task_budget = {"total": self.config.task_budget_tokens}

        session_store = self.config.session_store
        if session_store is None and (self.config.resume or self.config.fork_session):
            session_store = InMemorySessionStore()

        allowed_tools = list(dict.fromkeys(self.config.allowed_tools))
        for tool_name in self.skill_runtime.allowed_tools:
            if tool_name not in allowed_tools:
                allowed_tools.append(tool_name)
        if self.config.agents and "Task" not in allowed_tools:
            allowed_tools.append("Task")
        disallowed_tools = list(dict.fromkeys(self.config.disallowed_tools))
        base_tools = self.config.tools
        if self.config.agents and isinstance(base_tools, list) and "Task" not in base_tools:
            base_tools = [*base_tools, "Task"]
        if self._has_enabled_skills() and isinstance(base_tools, list) and "Skill" not in base_tools:
            base_tools = [*base_tools, "Skill"]
        setting_sources = self._effective_setting_sources()
        plugins = [
            *self.config.skill_plugins,
            *self.skill_runtime.plugins,
        ]

        can_use_tool = None if self.config.permission_mode == "dontAsk" else self.permission_policy.can_use_tool

        return ClaudeAgentOptions(
            model=self.config.model,
            cwd=self.config.project_root,
            tools=base_tools,
            mcp_servers={self.config.mcp_server_alias: self.mcp_server},
            strict_mcp_config=self.config.strict_mcp_config,
            setting_sources=setting_sources,
            permission_mode=self.config.permission_mode,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            hooks=self.hook_manager.to_sdk_hooks(),
            include_hook_events=self.config.include_hook_events,
            can_use_tool=can_use_tool,
            skills=self.skill_runtime.skills,
            plugins=plugins,
            max_turns=self.config.max_turns,
            max_budget_usd=self.config.max_budget_usd,
            output_format=output_format,
            agents=self.config.agents,
            system_prompt=self.config.system_prompt,
            session_store=session_store,
            session_store_flush=self.config.session_store_flush,
            resume=self.config.resume,
            fork_session=self.config.fork_session,
            task_budget=task_budget,
        )

    async def query(self, prompt: str, *, session_id: Optional[str] = None) -> SDKRunResult:
        """Send a prompt and collect a complete response."""
        client = self._require_client()
        await client.query(prompt, session_id=session_id or self.stage_run_id)
        result = await self.collect_response()
        self.validate_result(result)
        return result

    async def stream_query(
        self,
        prompt: str,
        *,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[Any]:
        """Send a prompt and yield raw SDK messages until ResultMessage."""
        client = self._require_client()
        await client.query(prompt, session_id=session_id or self.stage_run_id)
        async for message in client.receive_response():
            yield message

    async def collect_response(self) -> SDKRunResult:
        """Collect messages until the SDK emits ResultMessage."""
        client = self._require_client()
        messages: List[Any] = []
        text_parts: List[str] = []
        tool_calls_by_id: Dict[str, ToolCallRecord] = {}
        hook_events: List[HookEventMessage] = []
        result_message: ResultMessage | None = None

        async for message in client.receive_response():
            messages.append(message)
            self._collect_message(message, text_parts, tool_calls_by_id, hook_events)
            if isinstance(message, ResultMessage):
                result_message = message
                break

        if result_message is None:
            raise SDKRunValidationError("Claude SDK response ended without ResultMessage.")

        run_result = SDKRunResult(
            messages=messages,
            result_message=result_message,
            text="".join(text_parts),
            structured_output=result_message.structured_output,
            tool_calls=list(tool_calls_by_id.values()),
            hook_events=hook_events,
        )
        self._record_result_usage(run_result)
        return run_result

    def validate_result(self, result: SDKRunResult, *, require_structured: bool | None = None) -> None:
        """Validate result status, budgets, permissions, and structured output."""
        message = result.result_message
        if message.is_error:
            raise SDKRunValidationError(
                f"Claude SDK run failed: subtype={message.subtype}, errors={message.errors}, result={message.result}"
            )
        if message.terminal_reason not in (None, "completed"):
            raise SDKRunValidationError(f"Claude SDK run did not complete: {message.terminal_reason}")
        if not self.budget.check_budget():
            raise SDKRunValidationError(
                f"Budget exceeded: cost=${self.budget.current_cost_usd:.4f}, tokens={self.budget.current_tokens}"
            )
        if message.permission_denials:
            logger.warning("Permission denials: %s", message.permission_denials)
        should_require_structured = self.config.output_schema is not None if require_structured is None else require_structured
        if should_require_structured and result.structured_output is None:
            raise SDKRunValidationError("Claude SDK run did not return structured_output.")
        if self.config.output_schema is not None and result.structured_output is not None:
            _validate_json_schema(result.structured_output, self.config.output_schema)

    async def get_context_usage(self) -> Dict[str, Any]:
        """Return current SDK context usage."""
        client = self._require_client()
        return await client.get_context_usage()

    async def check_context_usage(self) -> Dict[str, Any]:
        """Read context usage and attach local warnings when thresholds are crossed."""
        usage = await self.get_context_usage()
        percentage = float(usage.get("percentage") or 0.0)
        if percentage >= self.config.context.warn_at_percent:
            logger.warning("Context usage is %.1f%%", percentage)
        if percentage >= self.config.context.compact_at_percent:
            usage["galatea_compaction_advice"] = self.context_compaction_instructions()
        return usage

    def context_compaction_instructions(self) -> str:
        """Stable compaction instruction for hooks, prompts, and diagnostics."""
        keys = ", ".join(self.config.context.preserve_keys)
        return (
            "When context is compacted, preserve all irreversible execution evidence: "
            f"{keys}. Drop large logs, raw samples, and duplicated tool output; keep URIs, digests, "
            "approval state, current objective, and unresolved errors."
        )

    async def interrupt(self) -> None:
        await self._require_client().interrupt()

    async def stop_task(self, task_id: str) -> None:
        await self._require_client().stop_task(task_id)

    async def set_permission_mode(self, mode: str) -> None:
        self.permission_policy.set_mode(mode)
        await self._require_client().set_permission_mode(mode)

    async def get_mcp_status(self) -> Dict[str, Any]:
        return await self._require_client().get_mcp_status()

    async def reconnect_mcp_server(self, server_name: str) -> None:
        await self._require_client().reconnect_mcp_server(server_name)

    async def toggle_mcp_server(self, server_name: str, enabled: bool) -> None:
        await self._require_client().toggle_mcp_server(server_name, enabled)

    def _create_default_hooks(self) -> HookManager:
        manager = HookManager(context=self.hook_context)
        manager.add_hook(HookEvent.SESSION_START, compact_context_hook)
        manager.add_hook(HookEvent.PRE_TOOL_USE, logging_hook)
        manager.add_hook(HookEvent.PRE_TOOL_USE, validation_hook)
        manager.add_hook(HookEvent.PRE_TOOL_USE, audit_hook)
        manager.add_hook(HookEvent.PRE_TOOL_USE, make_permission_hook(self.permission_policy))
        if self.config.disallowed_tools:
            manager.add_hook(
                HookEvent.PRE_TOOL_USE,
                deny_builtin_mutation_hook,
                matcher="|".join(self.config.disallowed_tools),
            )
        manager.add_hook(HookEvent.POST_TOOL_USE, logging_hook)
        manager.add_hook(HookEvent.POST_TOOL_USE, audit_hook)
        manager.add_hook(HookEvent.POST_TOOL_USE, summarize_large_tool_output_hook)
        manager.add_hook(HookEvent.POST_TOOL_USE_FAILURE, classify_tool_failure_hook)
        manager.add_hook(HookEvent.PRE_COMPACT, compact_context_hook)
        return manager

    def _policy_allowed_tools(self) -> List[str]:
        unqualified_mcp_tools = [
            name.rsplit("__", 1)[-1]
            for name in self.config.allowed_tools
            if name.startswith("mcp__")
        ]
        agent_tools = ["Task"] if self.config.agents else []
        return list(
            dict.fromkeys(
                [
                    *self.config.allowed_tools,
                    *self.skill_runtime.allowed_tools,
                    *agent_tools,
                    *unqualified_mcp_tools,
                ]
            )
        )

    def _resolve_skill_runtime(self) -> SkillRuntimeConfig:
        if self.config.skills is None:
            return SkillRuntimeConfig()
        return resolve_skill_runtime(
            self.config.project_root,
            self.config.skills,
            include_legacy_codex=self.config.include_legacy_codex_skills,
            include_plugin=self.config.include_skill_plugins,
            sync_legacy_codex=self.config.sync_legacy_codex_skills,
        )

    def _effective_setting_sources(self) -> Optional[List[str]]:
        if self.config.skills is None:
            return self.config.setting_sources
        if self.config.setting_sources == []:
            # Claude SDK only discovers project Skill dirs when project settings
            # are enabled; keep user/global settings isolated by default.
            return ["project"]
        return self.config.setting_sources

    def _has_enabled_skills(self) -> bool:
        skills = self.skill_runtime.skills
        return skills == "all" or (isinstance(skills, list) and len(skills) > 0)

    def _record_result_usage(self, result: SDKRunResult) -> None:
        try:
            self.budget.record_usage(result.total_cost_usd, result.total_tokens)
        except Exception:
            logger.exception("Failed to record SDK usage")
            raise

    def _collect_message(
        self,
        message: Any,
        text_parts: List[str],
        tool_calls_by_id: Dict[str, ToolCallRecord],
        hook_events: List[HookEventMessage],
    ) -> None:
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_calls_by_id[block.id] = ToolCallRecord(
                        tool_use_id=block.id,
                        name=block.name,
                        input=block.input,
                    )
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    record = tool_calls_by_id.get(block.tool_use_id)
                    if record is not None:
                        record.result = block.content
                        record.is_error = block.is_error
        elif isinstance(message, HookEventMessage):
            hook_events.append(message)

    def _require_client(self) -> ClaudeSDKClient:
        if self._client is None:
            raise RuntimeError("GalateaSDKRuntime is not connected. Use 'async with'.")
        return self._client


def result_to_json(result: SDKRunResult) -> str:
    """Serialize an SDKRunResult summary for CLI output or tests."""
    payload = {
        "session_id": result.result_message.session_id,
        "terminal_reason": result.result_message.terminal_reason,
        "total_cost_usd": result.total_cost_usd,
        "total_tokens": result.total_tokens,
        "structured_output": result.structured_output,
        "tool_calls": [
            {
                "tool_use_id": call.tool_use_id,
                "name": call.name,
                "input": call.input,
                "is_error": call.is_error,
            }
            for call in result.tool_calls
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def message_display_parts(message: Any) -> Dict[str, Any]:
    """Return normalized display metadata for chat UIs."""
    if isinstance(message, AssistantMessage):
        text = []
        tool_uses = []
        thinking = []
        for block in message.content:
            if isinstance(block, TextBlock):
                text.append(block.text)
            elif isinstance(block, ToolUseBlock):
                tool_uses.append({"id": block.id, "name": block.name, "input": block.input})
            elif isinstance(block, ThinkingBlock):
                thinking.append(block.thinking)
        return {"type": "assistant", "text": "".join(text), "tool_uses": tool_uses, "thinking": thinking}
    if isinstance(message, UserMessage):
        return {"type": "user", "content": message.content}
    if isinstance(message, ResultMessage):
        return {
            "type": "result",
            "subtype": message.subtype,
            "is_error": message.is_error,
            "cost": message.total_cost_usd,
            "usage": message.usage,
            "terminal_reason": message.terminal_reason,
        }
    if isinstance(message, HookEventMessage):
        return {"type": "hook", "event": message.hook_event_name, "subtype": message.subtype}
    if isinstance(message, (TaskStartedMessage, TaskProgressMessage, TaskNotificationMessage, TaskUpdatedMessage)):
        return {"type": "task", "subtype": getattr(message, "subtype", ""), "data": getattr(message, "data", {})}
    if isinstance(message, SystemMessage):
        return {"type": "system", "subtype": message.subtype, "data": message.data}
    return {"type": type(message).__name__, "value": str(message)}


def _validate_json_schema(value: Any, schema: Dict[str, Any], path: str = "$") -> None:
    """Small local schema validator for the subset used by stage contracts."""
    if "enum" in schema and value not in schema["enum"]:
        raise SDKRunValidationError(f"{path} must be one of {schema['enum']}, got {value!r}")

    if "anyOf" in schema:
        errors = []
        for variant in schema["anyOf"]:
            try:
                _validate_json_schema(value, variant, path)
                return
            except SDKRunValidationError as exc:
                errors.append(str(exc))
        raise SDKRunValidationError(f"{path} did not match any schema: {errors}")

    expected_type = schema.get("type")
    if expected_type is None:
        return

    if isinstance(expected_type, list):
        errors = []
        for type_name in expected_type:
            try:
                _validate_json_schema(value, {**schema, "type": type_name}, path)
                return
            except SDKRunValidationError as exc:
                errors.append(str(exc))
        raise SDKRunValidationError(f"{path} did not match any allowed type: {errors}")

    if expected_type == "object":
        if not isinstance(value, dict):
            raise SDKRunValidationError(f"{path} must be object")
        for key in schema.get("required", []):
            if key not in value:
                raise SDKRunValidationError(f"{path}.{key} is required")
        for key, child_schema in schema.get("properties", {}).items():
            if key in value:
                _validate_json_schema(value[key], child_schema, f"{path}.{key}")
    elif expected_type == "array":
        if not isinstance(value, list):
            raise SDKRunValidationError(f"{path} must be array")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                _validate_json_schema(item, item_schema, f"{path}[{index}]")
    elif expected_type == "string":
        if not isinstance(value, str):
            raise SDKRunValidationError(f"{path} must be string")
    elif expected_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise SDKRunValidationError(f"{path} must be integer")
    elif expected_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise SDKRunValidationError(f"{path} must be number")
    elif expected_type == "boolean":
        if not isinstance(value, bool):
            raise SDKRunValidationError(f"{path} must be boolean")
    elif expected_type == "null":
        if value is not None:
            raise SDKRunValidationError(f"{path} must be null")
