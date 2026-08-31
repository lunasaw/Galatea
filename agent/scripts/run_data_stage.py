#!/usr/bin/env python3
"""Data stage CLI placeholder with explicit unsupported status."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from agent.scripts.stage_status import print_stage_result, unsupported_stage_result
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent.scripts.stage_status import print_stage_result, unsupported_stage_result


PLANNED_TOOLS = [
    "mcp__galatea-platform__inspect_dataset_source",
    "mcp__galatea-platform__compute_source_manifest",
    "mcp__galatea-platform__submit_ray_data_job",
    "mcp__galatea-platform__validate_dataset_output",
    "mcp__galatea-platform__log_dataset_manifest",
]


def parse_args():
    """Parse command line arguments."""
    import argparse

    parser = argparse.ArgumentParser(description="Galatea data stage status")
    parser.add_argument("--json", action="store_true", help="Print structured JSON")
    return parser.parse_args()


def main() -> int:
    """Report that data-stage execution is not implemented yet."""
    args = parse_args()
    result = unsupported_stage_result("data", planned_tools=PLANNED_TOOLS)
    print_stage_result(result, as_json=args.json)
    return 2


if __name__ == "__main__":
    sys.exit(main())
