#!/usr/bin/env python3
"""
Galatea Agent Demo - Stage 1: Read-only Runtime POC

Demonstrates:
- Claude SDK runtime initialization
- In-process MCP server with read-only tools
- Platform inspection using agent
- Structured output (basic version)

This is a minimal POC for the agent architecture.
"""

import asyncio
import json
import sys
from pathlib import Path

# Add parent directory to path so we can import agent module
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.runtime import GalateaRuntime


async def demo_platform_inspection():
    """Demo: Platform inspection with read-only tools."""

    print("=" * 70)
    print("Galatea Agent Demo - Stage 1: Platform Inspection")
    print("=" * 70)
    print()

    project_root = Path("/data/ai/chenzhangyue/code/galatea")

    print(f"Project root: {project_root}")
    print(f"Initializing agent runtime with read-only inspection tools...")
    print()

    try:
        async with GalateaRuntime(project_root=project_root) as runtime:
            print("✓ Runtime initialized successfully")
            print("✓ MCP server created with inspection tools")
            print()

            print("-" * 70)
            print("Executing platform inspection...")
            print("-" * 70)
            print()

            result = await runtime.inspect_platform()

            print("=" * 70)
            print("INSPECTION RESULTS")
            print("=" * 70)
            print()
            print(f"Status: {result.get('status', 'unknown')}")
            print(f"Timestamp: {result.get('timestamp', 'N/A')}")
            print()

            if result.get('status') == 'success':
                print("Response:")
                print("-" * 70)
                response = result.get('response', '')
                # Handle both string and Message objects
                if hasattr(response, 'content'):
                    print(response.content)
                else:
                    print(response)
                print()
            else:
                print(f"Error: {result.get('error', 'Unknown error')}")
                print()

            print("=" * 70)
            print("Demo completed successfully!")
            print("=" * 70)

    except Exception as e:
        print()
        print("=" * 70)
        print("ERROR")
        print("=" * 70)
        print(f"Demo failed: {e}")
        print()
        import traceback
        traceback.print_exc()
        return 1

    return 0


async def demo_custom_query():
    """Demo: Custom query with read-only tools."""

    print()
    print("=" * 70)
    print("Custom Query Demo")
    print("=" * 70)
    print()

    project_root = Path("/data/ai/chenzhangyue/code/galatea")

    try:
        async with GalateaRuntime(project_root=project_root) as runtime:
            prompt = """List all training projects in the platform and show the structure
of the 'ray-cats-and-dogs' project. What config files does it have?"""

            print(f"Query: {prompt}")
            print()
            print("-" * 70)
            print("Agent response:")
            print("-" * 70)
            print()

            async for message in runtime.query(prompt):
                # Stream messages as they arrive
                if hasattr(message, 'content'):
                    print(message.content, end='', flush=True)
                else:
                    print(message, end='', flush=True)

            print()
            print()

    except Exception as e:
        print(f"Query failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


def main():
    """Run all demos."""
    print()
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 15 + "GALATEA AGENT ARCHITECTURE DEMO" + " " * 21 + "║")
    print("║" + " " * 20 + "Stage 1: Read-only Runtime POC" + " " * 18 + "║")
    print("╚" + "=" * 68 + "╝")
    print()

    # Run platform inspection demo
    result = asyncio.run(demo_platform_inspection())

    if result == 0:
        # If successful, run custom query demo
        result = asyncio.run(demo_custom_query())

    return result


if __name__ == "__main__":
    exit(main())
