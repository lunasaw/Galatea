#!/usr/bin/env python3
"""Training stage CLI placeholder with explicit unsupported status."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from agent.scripts.stage_status import print_stage_result, unsupported_stage_result
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent.scripts.stage_status import print_stage_result, unsupported_stage_result


PLANNED_TOOLS = [
    "mcp__galatea-platform__validate_training_config",
    "mcp__galatea-platform__inspect_mlflow_runs",
    "mcp__galatea-platform__submit_ray_training_job",
    "mcp__galatea-platform__verify_checkpoint",
    "mcp__galatea-platform__summarize_training_result",
]


def parse_args():
    """Parse command line arguments."""
    import argparse

    parser = argparse.ArgumentParser(description="Galatea training stage status")
    parser.add_argument("--json", action="store_true", help="Print structured JSON")
    return parser.parse_args()


def main() -> int:
    """Report that training-stage execution is not implemented yet."""
    args = parse_args()
    result = unsupported_stage_result("training", planned_tools=PLANNED_TOOLS)
    print_stage_result(result, as_json=args.json)
    return 2


if __name__ == "__main__":
    sys.exit(main())
