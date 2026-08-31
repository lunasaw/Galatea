#!/usr/bin/env python3
"""
Quick demonstration of Galatea Agent capabilities.

Shows both direct tool usage and full agent runtime.
"""

import asyncio
import sys
import logging
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Configure logging to show model serialization
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


def demo_tools_direct():
    """Demo: Direct tool usage without agent."""
    print("=" * 70)
    print("Demo 1: Direct Tool Usage")
    print("=" * 70)
    print()

    from agent.tools.inspection import list_training_projects, inspect_project_structure

    root = "/data/ai/chenzhangyue/code/galatea"

    # List projects
    projects = list_training_projects(root)
    print(f"Training projects: {', '.join(projects)}")
    print()

    # Inspect one project
    info = inspect_project_structure(root, "ray-cats-and-dogs")
    print(f"ray-cats-and-dogs configs: {', '.join(info['config_files'])}")
    print()


async def demo_agent_query():
    """Demo: Agent with custom query."""
    print("=" * 70)
    print("Demo 2: Agent Query")
    print("=" * 70)
    print()

    from agent.runtime import GalateaRuntime

    project_root = Path("/data/ai/chenzhangyue/code/galatea")

    prompt = "List the training projects and tell me about ray-cats-and-dogs."

    print(f"Query: {prompt}")
    print()
    print("Agent response:")
    print("-" * 70)

    try:
        async with GalateaRuntime(project_root=project_root) as runtime:
            # Stream response (will show thinking, tool calls, etc.)
            async for message in runtime.query(prompt):
                # Only print ResultMessage at the end
                if hasattr(message, 'result'):
                    print(message.result)
                    break
        print()
    except Exception as e:
        print(f"Note: Full agent demo requires ANTHROPIC_API_KEY")
        print(f"Error: {e}")
        print()


def main():
    """Run quick demos."""
    print()
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 18 + "GALATEA AGENT QUICK DEMO" + " " * 26 + "║")
    print("╚" + "=" * 68 + "╝")
    print()

    # Demo 1: Direct tool usage (always works)
    demo_tools_direct()

    # Demo 2: Agent query (requires API key)
    print("To run agent demo with Claude API, set ANTHROPIC_API_KEY")
    print("Example: export ANTHROPIC_API_KEY='your-key'")
    print()

    # Uncomment to run agent demo:
    # asyncio.run(demo_agent_query())

    print("=" * 70)
    print("✅ Quick demo complete!")
    print()
    print("For full demo: python agent/demo/demo_basic.py")
    print("For tool tests: python agent/test/test_tools_direct.py")
    print("=" * 70)


if __name__ == "__main__":
    main()
