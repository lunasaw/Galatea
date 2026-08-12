#!/usr/bin/env python3
"""
Platform Inspector - Using Claude SDK

Inspects Galatea platform health and status using the PLATFORM_INSPECTOR agent.

Usage:
    python agent/scripts/inspect_platform.py
    python agent/scripts/inspect_platform.py --detailed
"""

import asyncio
import sys
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)


def parse_args():
    """Parse command line arguments"""
    import argparse

    parser = argparse.ArgumentParser(description="Galatea Platform Inspector")
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Show detailed inspection report"
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Project root directory (default: current directory)"
    )

    return parser.parse_args()


async def inspect_platform(detailed: bool = False, project_root: Path = Path.cwd()):
    """
    Inspect platform using PLATFORM_INSPECTOR agent

    Args:
        detailed: Show detailed inspection
        project_root: Galatea project root
    """
    sys.path.insert(0, str(project_root))

    # Import after adding to path
    from agent.tools.server import create_galatea_mcp_server
    from agent.agents import PLATFORM_INSPECTOR

    print("\n" + "=" * 70)
    print("🔍 Galatea Platform Inspector")
    print("=" * 70)
    print()

    # Create SDK options with PLATFORM_INSPECTOR agent
    options = ClaudeAgentOptions(
        mcp_servers={"galatea": create_galatea_mcp_server()},
        agents={"inspector": PLATFORM_INSPECTOR},
        permission_mode="dontAsk",  # Auto-approve read-only tools
        cwd=project_root,
    )

    async with ClaudeSDKClient(options) as client:
        # Build inspection query
        if detailed:
            query = """Use the inspector agent to perform a detailed platform inspection:

1. List all training projects in train-model/
2. Check health of key services:
   - MLflow (port 5000)
   - MinIO (port 9000)
   - Ray cluster
3. For each project, inspect its structure
4. Report any issues or warnings

Provide a comprehensive report."""
        else:
            query = """Use the inspector agent to check platform health:

1. Check service health (MLflow, MinIO, Ray)
2. List training projects
3. Summarize overall status

Keep the report concise."""

        # Send query
        await client.query(query)

        # Receive and display response
        current_text = ""
        tools_used = []

        async for msg in client.receive_response():
            # Skip system messages
            if isinstance(msg, SystemMessage):
                continue

            # Handle assistant messages
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    # Skip thinking blocks
                    if type(block).__name__ == 'ThinkingBlock':
                        continue

                    # Handle text blocks - stream incrementally
                    if isinstance(block, TextBlock):
                        if block.text != current_text:
                            new_text = block.text[len(current_text):]
                            print(new_text, end="", flush=True)
                            current_text = block.text

                    # Track tool usage
                    elif isinstance(block, ToolUseBlock):
                        tools_used.append(block.name)
                        if detailed:
                            print(f"\n   [Using tool: {block.name}]", end="", flush=True)

            # Handle result message
            elif isinstance(msg, ResultMessage):
                print()  # End line
                print()

                # Show summary
                if tools_used:
                    print(f"Tools used: {', '.join(set(tools_used))}")

                if msg.total_cost_usd:
                    print(f"Cost: ${msg.total_cost_usd:.4f}")

                print()
                break

    print("=" * 70)
    print()


def main():
    """Main entry point"""
    args = parse_args()

    try:
        asyncio.run(inspect_platform(
            detailed=args.detailed,
            project_root=args.project_root,
        ))
        return 0
    except KeyboardInterrupt:
        print("\n\nInterrupted by user\n")
        return 130
    except Exception as e:
        print(f"\n❌ Error: {e}\n")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
