"""
Galatea agents 的 MCP 工具服务器。

创建一个带有只读检查工具的进程内 MCP 服务器。
"""

from typing import Any, Dict
from claude_agent_sdk import create_sdk_mcp_server, tool

from .inspection import (
    inspect_project_structure,
    check_service_health,
    inspect_mlflow_experiment,
    inspect_ray_status,
    list_training_projects,
)


# 使用 SDK 装饰器定义 MCP 工具
# 工具必须是异步的，并返回带有 "content" 键的字典

@tool(
    "inspect_project_structure",
    "检查训练项目的结构。返回项目路径、配置文件、脚本文件和测试目录。只读操作，无副作用。",
    {
        "project_root": str,
        "project_name": str,
    }
)
async def tool_inspect_project_structure(args: Dict[str, Any]) -> Dict[str, Any]:
    """检查项目结构。"""
    result = inspect_project_structure(args["project_root"], args["project_name"])
    import json
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@tool(
    "check_service_health",
    "检查平台服务是否活动。验证 systemd 服务状态。只读操作，无副作用。",
    {
        "service_name": str,
        "port": int,
    }
)
async def tool_check_service_health(args: Dict[str, Any]) -> Dict[str, Any]:
    """检查服务健康状况。"""
    result = check_service_health(
        args["service_name"],
        args["port"],
        args.get("endpoint", "127.0.0.1")
    )
    import json
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@tool(
    "inspect_mlflow_experiment",
    "检查 MLflow 实验。返回实验元数据、artifact 位置和运行计数。使用 MLflow Tracking API 的只读操作。",
    {
        "tracking_uri": str,
        "experiment_name": str,
    }
)
async def tool_inspect_mlflow_experiment(args: Dict[str, Any]) -> Dict[str, Any]:
    """检查 MLflow 实验。"""
    result = inspect_mlflow_experiment(args["tracking_uri"], args["experiment_name"])
    import json
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@tool(
    "inspect_ray_status",
    "检查 Ray 集群状态。返回集群可用性和基本资源信息。只读操作。",
    {}
)
async def tool_inspect_ray_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """检查 Ray 状态。"""
    result = inspect_ray_status()
    import json
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@tool(
    "list_training_projects",
    "列出 train-model 目录中的所有训练项目。返回项目名称。只读操作。",
    {
        "project_root": str,
    }
)
async def tool_list_training_projects(args: Dict[str, Any]) -> Dict[str, Any]:
    """列出训练项目。"""
    projects = list_training_projects(args["project_root"])
    result = {"projects": projects, "count": len(projects)}
    import json
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


INSPECTION_TOOLS = [
    tool_inspect_project_structure,
    tool_check_service_health,
    tool_inspect_mlflow_experiment,
    tool_inspect_ray_status,
    tool_list_training_projects,
]


def create_galatea_mcp_server():
    """
    创建带有 Galatea 检查工具的进程内 MCP 服务器。

    Returns:
        配置了检查工具的 MCP 服务器实例
    """
    return create_sdk_mcp_server(
        name="galatea-platform",
        tools=INSPECTION_TOOLS,
    )
