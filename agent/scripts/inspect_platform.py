#!/usr/bin/env python3
"""CLI entry point for Galatea platform inspection through the SDK runtime."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

try:
    from agent.runtime import GalateaRuntime
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent.runtime import GalateaRuntime


def parse_args():
    """Parse command line arguments."""
    import argparse

    parser = argparse.ArgumentParser(description="Galatea Platform Inspector")
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Show detailed inspection report",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    return parser.parse_args()


async def inspect_platform(
    detailed: bool = False,
    project_root: Path = Path.cwd(),
) -> dict[str, Any]:
    """Inspect the platform via the unified Galatea runtime."""
    project_root = Path(project_root)

    print("\n" + "=" * 70)
    print("Galatea Platform Inspector")
    print("=" * 70)
    print()

    async with GalateaRuntime(project_root, auto_load_config=True) as runtime:
        result = await runtime.inspect_platform(detailed=detailed)

    response = str(result.get("response") or "").strip()
    if response:
        print(response)
        print()

    tool_calls = result.get("tool_calls") or []
    if tool_calls:
        print(f"Tools used: {', '.join(sorted(set(tool_calls)))}")

    cost_usd = result.get("cost_usd")
    if cost_usd:
        print(f"Cost: ${float(cost_usd):.4f}")

    print()
    print("=" * 70)
    print()
    return result


def main() -> int:
    """Main entry point."""
    args = parse_args()

    try:
        asyncio.run(
            inspect_platform(
                detailed=args.detailed,
                project_root=args.project_root,
            )
        )
        return 0
    except KeyboardInterrupt:
        print("\n\nInterrupted by user\n")
        return 130
    except Exception as exc:
        print(f"\nError: {exc}\n")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
