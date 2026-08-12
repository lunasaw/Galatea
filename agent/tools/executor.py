"""Direct tool registry and executor for non-LLM tests and deterministic flows."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from agent.hooks import HookContext, HookEvent, HookInput, HookManager
from agent.policies import PermissionDeniedError, PermissionPolicy


ToolHandler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]] | Dict[str, Any]]


@dataclass
class ToolSpec:
    """A deterministic tool definition independent of Claude SDK transport."""

    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: ToolHandler
    read_only: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolExecutionResult:
    """Result returned by ToolExecutor."""

    tool_name: str
    content: Any
    is_error: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    """In-process registry for direct tool execution."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def unregister(self, name: str) -> None:
        self._tools.pop(name)

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Tool not found: {name}") from exc

    def list(self) -> List[str]:
        return sorted(self._tools)


class ToolExecutor:
    """Run registered tools through permission checks and local hooks."""

    def __init__(
        self,
        registry: ToolRegistry,
        permission_policy: PermissionPolicy,
        hook_manager: Optional[HookManager] = None,
        context: Optional[HookContext] = None,
    ) -> None:
        self.registry = registry
        self.permission_policy = permission_policy
        self.context = context or HookContext(session_id="direct", agent_type="tool-executor")
        self.hook_manager = hook_manager or HookManager(context=self.context)

    async def execute(self, tool_name: str, tool_input: Dict[str, Any]) -> ToolExecutionResult:
        spec = self.registry.get(tool_name)
        pre_input = HookInput(
            event=HookEvent.PRE_TOOL_USE,
            data={"tool_name": tool_name, "tool_input": tool_input},
            tool_name=tool_name,
            tool_input=tool_input,
        )
        pre_outputs = await self.hook_manager.invoke_hooks(
            HookEvent.PRE_TOOL_USE,
            pre_input,
            self.context,
        )
        for output in pre_outputs:
            if output.permission_decision == "deny":
                reason = output.permission_decision_reason or output.reason or "Denied by hook"
                raise PermissionDeniedError(tool_name, reason)

        behavior = self.permission_policy.check_permission(tool_name, tool_input)
        if behavior != "allow":
            raise PermissionDeniedError(
                tool_name,
                self.permission_policy.explain_permission(tool_name, tool_input),
            )

        try:
            response = spec.handler(tool_input)
            if inspect.isawaitable(response):
                response = await response
            result = ToolExecutionResult(
                tool_name=tool_name,
                content=response.get("content", response),
                is_error=bool(response.get("is_error", False)),
                metadata={"read_only": spec.read_only, **spec.metadata},
            )
        except Exception as exc:  # noqa: BLE001 - direct executor must normalize tool failures
            failure_input = HookInput(
                event=HookEvent.POST_TOOL_USE_FAILURE,
                data={"tool_name": tool_name, "tool_input": tool_input, "error": str(exc)},
                tool_name=tool_name,
                tool_input=tool_input,
            )
            await self.hook_manager.invoke_hooks(HookEvent.POST_TOOL_USE_FAILURE, failure_input, self.context)
            return ToolExecutionResult(
                tool_name=tool_name,
                content=[{"type": "text", "text": str(exc)}],
                is_error=True,
                metadata={"exception_type": type(exc).__name__},
            )

        post_input = HookInput(
            event=HookEvent.POST_TOOL_USE,
            data={
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_response": result.content,
            },
            tool_name=tool_name,
            tool_input=tool_input,
            tool_response=result.content,
        )
        post_outputs = await self.hook_manager.invoke_hooks(HookEvent.POST_TOOL_USE, post_input, self.context)
        for output in post_outputs:
            if output.updated_mcp_tool_output is not None:
                result.content = output.updated_mcp_tool_output.get("content", output.updated_mcp_tool_output)
            elif output.updated_tool_output is not None:
                result.content = output.updated_tool_output
        return result


def mcp_content_json(data: Any) -> Dict[str, Any]:
    """Return an MCP-compatible text content payload."""
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]}
