"""Galatea Agent runtime built on ClaudeSDKClient."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from claude_agent_sdk import ClaudeSDKClient

from agent.core import (
    AgentSDKConfig,
    GalateaSDKRuntime,
    SDKRunResult,
    message_display_parts,
)

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
        if output_schema:
            final_prompt = (
                f"{prompt}\n\nReturn structured JSON matching this schema:\n"
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
        return await self.sdk_runtime.query(prompt)

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
