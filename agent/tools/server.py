"""
MCP tool server for Galatea agents.

Creates an in-process MCP server with read-only inspection tools.
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


# Define MCP tools using SDK decorator
# Tools must be async and return dict with "content" key

@tool(
    "inspect_project_structure",
    "Inspect the structure of a training project. Returns project path, config files, script files, and test directories. Read-only operation with no side effects.",
    {
        "project_root": str,
        "project_name": str,
    }
)
async def tool_inspect_project_structure(args: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect project structure."""
    result = inspect_project_structure(args["project_root"], args["project_name"])
    import json
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@tool(
    "check_service_health",
    "Check if a platform service is active. Verifies systemd service status. Read-only operation with no side effects.",
    {
        "service_name": str,
        "port": int,
    }
)
async def tool_check_service_health(args: Dict[str, Any]) -> Dict[str, Any]:
    """Check service health."""
    result = check_service_health(
        args["service_name"],
        args["port"],
        args.get("endpoint", "127.0.0.1")
    )
    import json
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@tool(
    "inspect_mlflow_experiment",
    "Inspect an MLflow experiment. Returns experiment metadata, artifact location, and run count. Read-only operation using MLflow Tracking API.",
    {
        "tracking_uri": str,
        "experiment_name": str,
    }
)
async def tool_inspect_mlflow_experiment(args: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect MLflow experiment."""
    result = inspect_mlflow_experiment(args["tracking_uri"], args["experiment_name"])
    import json
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@tool(
    "inspect_ray_status",
    "Check Ray cluster status. Returns cluster availability and basic resource information. Read-only operation.",
    {}
)
async def tool_inspect_ray_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """Check Ray status."""
    result = inspect_ray_status()
    import json
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@tool(
    "list_training_projects",
    "List all training projects in train-model directory. Returns project names. Read-only operation.",
    {
        "project_root": str,
    }
)
async def tool_list_training_projects(args: Dict[str, Any]) -> Dict[str, Any]:
    """List training projects."""
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
    Create an in-process MCP server with Galatea inspection tools.

    Returns:
        MCP server instance configured with inspection tools
    """
    return create_sdk_mcp_server(
        name="galatea-platform",
        tools=INSPECTION_TOOLS,
    )
