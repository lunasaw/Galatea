"""Shared tool allowlist helpers for Galatea commands."""

from __future__ import annotations

from agent.core import CLAUDE_CODE_BASE_ALLOWED_TOOLS

CLAUDE_CODE_READ_ONLY_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "LS",
]


def default_platform_allowed_tools(alias: str = "galatea-platform") -> list[str]:
    """Return Galatea platform MCP inspection tools."""
    return [
        f"mcp__{alias}__list_training_projects",
        f"mcp__{alias}__inspect_project_structure",
        f"mcp__{alias}__check_service_health",
        f"mcp__{alias}__inspect_mlflow_experiment",
        f"mcp__{alias}__inspect_ray_status",
    ]


def claude_code_allowed_tools(alias: str = "galatea-platform") -> list[str]:
    """Return all Galatea inspection tools plus base Claude Code tools."""
    return list(
        dict.fromkeys(
            [
                *default_platform_allowed_tools(alias),
                *CLAUDE_CODE_BASE_ALLOWED_TOOLS,
            ]
        )
    )


def claude_code_read_only_allowed_tools(alias: str = "galatea-platform") -> list[str]:
    """Return Galatea inspection tools plus safe read-only Claude Code tools."""
    return list(
        dict.fromkeys(
            [
                *default_platform_allowed_tools(alias),
                *CLAUDE_CODE_READ_ONLY_TOOLS,
            ]
        )
    )
