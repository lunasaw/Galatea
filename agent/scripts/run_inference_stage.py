#!/usr/bin/env python3
"""Inference stage CLI placeholder with explicit unsupported status."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from agent.scripts.stage_status import print_stage_result, unsupported_stage_result
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent.scripts.stage_status import print_stage_result, unsupported_stage_result


PLANNED_TOOLS = [
    "mcp__galatea-platform__load_model_artifact_metadata",
    "mcp__galatea-platform__verify_artifact_recovery",
    "mcp__galatea-platform__run_smoke_inference",
    "mcp__galatea-platform__evaluate_quality_gates",
    "mcp__galatea-platform__request_promotion_approval",
]


def parse_args():
    """Parse command line arguments."""
    import argparse

    parser = argparse.ArgumentParser(description="Galatea inference stage status")
    parser.add_argument("--json", action="store_true", help="Print structured JSON")
    return parser.parse_args()


def main() -> int:
    """Report that inference-stage execution is not implemented yet."""
    args = parse_args()
    result = unsupported_stage_result("inference", planned_tools=PLANNED_TOOLS)
    print_stage_result(result, as_json=args.json)
    return 2


if __name__ == "__main__":
    sys.exit(main())
