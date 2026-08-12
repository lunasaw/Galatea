"""
Galatea Agent Tools

MCP tools for platform operations.
"""

from .server import create_galatea_mcp_server, INSPECTION_TOOLS
from .inspection import (
    inspect_project_structure,
    check_service_health,
    inspect_mlflow_experiment,
    inspect_ray_status,
    list_training_projects,
)

__all__ = [
    "create_galatea_mcp_server",
    "INSPECTION_TOOLS",
    "inspect_project_structure",
    "check_service_health",
    "inspect_mlflow_experiment",
    "inspect_ray_status",
    "list_training_projects",
]
