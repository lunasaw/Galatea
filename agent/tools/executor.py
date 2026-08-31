"""Deterministic SDK MCP-tool harness for unit tests and offline CI only.

Production agent calls must use ``create_galatea_mcp_server`` through
``ClaudeSDKClient``. This harness intentionally has no hooks, MCP lifecycle,
or model-facing dispatch and is not a second agent runtime.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import SdkMcpTool

from agent.policies import PermissionDeniedError, PermissionPolicy


@dataclass
class ToolExecutionResult:
    """Normalized result from an offline SDK MCP-tool call."""

    tool_name: str
    content: Any
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class SdkMcpToolRegistry:
    """Index the same ``SdkMcpTool`` objects registered with the MCP server."""

    def __init__(self, tools: list[SdkMcpTool[Any]] | None = None) -> None:
        self._tools: dict[str, SdkMcpTool[Any]] = {}
        for sdk_tool in tools or []:
            self.register(sdk_tool)

    def register(self, sdk_tool: SdkMcpTool[Any]) -> None:
        if not isinstance(sdk_tool, SdkMcpTool):
            raise TypeError("Offline registry accepts Claude SDK SdkMcpTool objects only.")
        if sdk_tool.name in self._tools:
            raise ValueError(f"Tool already registered: {sdk_tool.name}")
        self._tools[sdk_tool.name] = sdk_tool

    def get(self, name: str) -> SdkMcpTool[Any]:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Tool not found: {name}") from exc

    def list(self) -> list[str]:
        return sorted(self._tools)


class DeterministicMcpToolExecutor:
    """Call SDK MCP handlers directly for tests, with fail-closed local rules."""

    def __init__(
        self,
        registry: SdkMcpToolRegistry,
        permission_policy: PermissionPolicy,
    ) -> None:
        self.registry = registry
        self.permission_policy = permission_policy

    async def execute(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> ToolExecutionResult:
        sdk_tool = self.registry.get(tool_name)
        if self.permission_policy.check_permission(tool_name, tool_input) != "allow":
            raise PermissionDeniedError(
                tool_name,
                self.permission_policy.explain_permission(tool_name, tool_input),
            )

        try:
            response = sdk_tool.handler(tool_input)
            if inspect.isawaitable(response):
                response = await response
            annotations = (
                sdk_tool.annotations.model_dump(exclude_none=True)
                if sdk_tool.annotations is not None
                else {}
            )
            return ToolExecutionResult(
                tool_name=tool_name,
                content=response.get("content", response),
                is_error=bool(response.get("is_error", False)),
                metadata={"mcp_annotations": annotations},
            )
        except Exception as exc:  # noqa: BLE001 - offline harness normalizes failures
            return ToolExecutionResult(
                tool_name=tool_name,
                content=[{"type": "text", "text": str(exc)}],
                is_error=True,
                metadata={"exception_type": type(exc).__name__},
            )


def inspection_test_executor() -> DeterministicMcpToolExecutor:
    """Build an offline executor from the production inspection MCP catalog."""
    from agent.tools.server import INSPECTION_TOOLS

    registry = SdkMcpToolRegistry(INSPECTION_TOOLS)
    policy = PermissionPolicy.for_galatea(
        allowed_tools=registry.list(),
        disallowed_tools=[],
    )
    return DeterministicMcpToolExecutor(registry, policy)
