"""Shared helpers for stage CLIs whose execution tools are not implemented."""

from __future__ import annotations

import json
from typing import Any


def unsupported_stage_result(stage: str, *, planned_tools: list[str]) -> dict[str, Any]:
    """Return a structured result for a planned-but-not-implemented stage."""
    return {
        "status": "unsupported",
        "stage": stage,
        "reason": "Stage-specific MCP execution tools are not implemented or registered.",
        "planned_tools": planned_tools,
        "safe_current_entrypoint": "agent/scripts/inspect_platform.py",
        "sdk_boundary": "Use GalateaRuntime and SDK MCP tools; do not bypass with Bash or direct service writes.",
    }


def print_stage_result(result: dict[str, Any], *, as_json: bool = False) -> None:
    """Print a stage status result for CLI users."""
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"{result['stage'].title()} Stage")
    print("=" * 50)
    print(f"Status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print()
    print("Planned SDK MCP tools:")
    for tool_name in result["planned_tools"]:
        print(f"  - {tool_name}")
    print()
    print(f"Current safe entrypoint: {result['safe_current_entrypoint']}")
