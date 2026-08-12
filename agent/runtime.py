"""
Galatea Agent 运行时

封装 ClaudeSDKClient 并提供平台特定配置：
- 进程内 MCP 服务器与 Galatea 工具集成
- 结构化输出模式验证
- 会话管理
- 权限控制
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, AsyncIterator
from datetime import datetime

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, UserMessage

from agent.tools.server import create_galatea_mcp_server
from agent.config import apply_anthropic_config_to_env

# 配置模型序列化日志记录器
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def _serialize_to_oneline(obj: Any) -> str:
    """
    将对象序列化为单行 JSON，不截断。

    Args:
        obj: 要序列化的对象（字典、列表或任何可 JSON 序列化的对象）

    Returns:
        单行 JSON 字符串，无长度限制
    """
    try:
        # 处理具有 __dict__ 属性的对象
        if hasattr(obj, '__dict__'):
            serializable = {}
            for key, value in obj.__dict__.items():
                if not key.startswith('_'):
                    try:
                        # 尝试序列化该值
                        json.dumps(value)
                        serializable[key] = value
                    except (TypeError, ValueError):
                        # 如果不可序列化，转换为字符串
                        serializable[key] = str(value)
            obj = serializable

        # 序列化为无缩进、无空格、无长度限制的格式
        return json.dumps(obj, separators=(',', ':'), ensure_ascii=False, default=str)
    except Exception as e:
        # 回退到字符串表示
        return f"<serialization_error: {str(e)}, repr: {repr(obj)[:1000]}>"


class GalateaRuntime:
    """
    Galatea Agent 操作的运行时包装器。

    管理 Claude SDK 客户端生命周期、工具注册和结构化输出验证。
    """

    def __init__(
        self,
        project_root: Path,
        mlflow_tracking_uri: str = "http://127.0.0.1:5000",
        model: str = "claude-opus-5",
        auto_load_config: bool = True,
    ):
        """
        初始化 Galatea 运行时。

        Args:
            project_root: Galatea 平台的根目录
            mlflow_tracking_uri: MLflow 跟踪服务器 URI
            model: 要使用的 Claude 模型
            auto_load_config: 如果为 True，当环境中不存在时，自动从 ~/.claude/settings.json
                加载 ANTHROPIC_API_KEY 和 ANTHROPIC_BASE_URL
        """
        self.project_root = project_root
        self.mlflow_uri = mlflow_tracking_uri
        self.model = model

        # 如果需要，从 settings.json 加载 Anthropic 配置
        if auto_load_config:
            apply_anthropic_config_to_env()

        # 创建带有检查工具的 MCP 服务器
        self.mcp_server = create_galatea_mcp_server()

        # Claude SDK 客户端（在 __aenter__ 中初始化）
        self._client: Optional[ClaudeSDKClient] = None

    async def __aenter__(self):
        """进入异步上下文管理器。"""
        # 创建 Claude SDK 选项
        options = ClaudeAgentOptions(
            model=self.model,
            mcp_servers={"galatea-platform": self.mcp_server},
            permission_mode="dontAsk",  # 阶段 1：仅限只读工具
            cwd=self.project_root,
        )

        # 初始化 Claude SDK 客户端
        self._client = ClaudeSDKClient(options)
        await self._client.__aenter__()

        return self

    async def __aexit__(self, *args):
        """退出异步上下文管理器。"""
        if self._client:
            await self._client.__aexit__(*args)

    async def query(
        self,
        prompt: str,
        output_schema: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[Any]:
        """
        执行查询并流式传输响应消息。

        Args:
            prompt: 查询提示
            output_schema: 可选的结构化输出 JSON 模式

        Yields:
            来自 agent 的响应消息
        """
        if not self._client:
            raise RuntimeError("运行时未初始化。请使用 'async with' 上下文。")

        # 如果提供了模式，添加结构化输出请求
        final_prompt = prompt
        if output_schema:
            schema_instruction = (
                f"\n\n重要：将您的响应作为结构化 JSON 返回，"
                f"匹配此模式：\n{output_schema}"
            )
            final_prompt = prompt + schema_instruction

        # 记录请求序列化（压缩，不截断）
        request_data = {
            "type": "request",
            "model": self.model,
            "timestamp": datetime.utcnow().isoformat(),
            "prompt": final_prompt,
            "output_schema": output_schema,
        }
        logger.info(f"MODEL_REQUEST: {_serialize_to_oneline(request_data)}")

        # 发送查询
        await self._client.query(final_prompt)

        # 流式传输响应
        async for message in self._client.receive_response():
            # 记录响应序列化（压缩，不截断）
            response_data = {
                "type": "response",
                "timestamp": datetime.utcnow().isoformat(),
                "message": message,
            }
            logger.info(f"MODEL_RESPONSE: {_serialize_to_oneline(response_data)}")

            yield message

    async def inspect_platform(self) -> Dict[str, Any]:
        """
        使用 agent 检查 Galatea 平台状态。

        Returns:
            平台检查结果
        """
        prompt = f"""检查位于 {self.project_root} 的 Galatea ML 训练平台。

请使用可用的检查工具来检查：
1. 列出 train-model/ 中的所有训练项目
2. 检查关键服务的健康状况：mlflow（端口 5000）、minio（端口 9000）
3. 检查 Ray 集群状态
4. 对于 'ray-cats-and-dogs' 项目，检查其结构

在清晰的报告中总结您的发现。"""

        messages = []
        async for message in self.query(prompt):
            messages.append(message)

        # 提取最终文本响应
        if messages:
            last_message = messages[-1]
            return {
                "status": "success",
                "response": last_message.content if hasattr(last_message, 'content') else str(last_message),
                "timestamp": datetime.utcnow().isoformat(),
            }

        return {
            "status": "failed",
            "error": "未收到响应",
            "timestamp": datetime.utcnow().isoformat(),
        }
