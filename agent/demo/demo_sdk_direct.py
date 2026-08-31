#!/usr/bin/env python3
"""
Low-level Claude SDK Usage with Galatea Tools

This file demonstrates the SDK primitives underneath ``GalateaRuntime``.
Application and production entry points should use ``GalateaRuntime`` so the
shared hooks, budgets, result validation, and permission boundaries stay active.

Usage:
    python agent/demo/demo_sdk_direct.py
"""

import asyncio
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)

from agent.agents.definitions import GALATEA_TOOL_PREFIX
from agent.policies.permission import DEFAULT_DISALLOWED_TOOLS
from agent.runtime import default_platform_allowed_tools
from agent.tools.server import create_galatea_mcp_server


def low_level_options(*, agents=None):
    """Build a complete, isolated option set for this low-level demo."""
    allowed_tools = default_platform_allowed_tools()
    tools = []
    if agents:
        tools.append("Task")
        allowed_tools.append("Task")
    return ClaudeAgentOptions(
        model="claude-opus-5",
        tools=tools,
        allowed_tools=allowed_tools,
        disallowed_tools=list(DEFAULT_DISALLOWED_TOOLS),
        mcp_servers={"galatea-platform": create_galatea_mcp_server()},
        strict_mcp_config=True,
        setting_sources=["project"] if agents else [],
        permission_mode="dontAsk",
        agents=agents,
        cwd=Path.cwd(),
    )


def display_message(msg):
    """Display message with proper formatting"""
    if isinstance(msg, SystemMessage):
        # Skip system messages
        return

    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            # Skip thinking blocks
            if type(block).__name__ == 'ThinkingBlock':
                continue

            if isinstance(block, TextBlock):
                print(f"Claude: {block.text}")
            elif isinstance(block, ToolUseBlock):
                print(f"  🔧 Using tool: {block.name}")

    elif isinstance(msg, ResultMessage):
        if msg.total_cost_usd:
            print(f"💰 Cost: ${msg.total_cost_usd:.4f}")
        print()


async def example_basic_query():
    """Example 1: Basic query with Galatea tools"""
    print("=" * 70)
    print("Example 1: Basic Query with Galatea Tools")
    print("=" * 70)
    print()

    options = low_level_options()

    async with ClaudeSDKClient(options=options) as client:
        print("User: List all training projects")
        print()

        await client.query("List all training projects in train-model/")

        async for msg in client.receive_response():
            display_message(msg)


async def example_with_agent_definition():
    """Example 2: Using AgentDefinition for specialized agent"""
    print("=" * 70)
    print("Example 2: Using AgentDefinition")
    print("=" * 70)
    print()

    options = low_level_options(
        agents={
            "platform-inspector": AgentDefinition(
                description="Inspects Galatea platform health and status",
                prompt="You are a platform inspector. Check service health, list projects, "
                       "and report status clearly and concisely.",
                tools=[
                    f"{GALATEA_TOOL_PREFIX}list_training_projects",
                    f"{GALATEA_TOOL_PREFIX}check_service_health",
                    f"{GALATEA_TOOL_PREFIX}inspect_ray_status",
                ],
                disallowedTools=list(DEFAULT_DISALLOWED_TOOLS),
                model="sonnet",  # Use faster model for inspection
                permissionMode="dontAsk",
            )
        }
    )

    async with ClaudeSDKClient(options=options) as client:
        print("User: Use platform-inspector agent to check the platform")
        print()

        await client.query("Use the platform-inspector agent to check platform health")

        async for msg in client.receive_response():
            display_message(msg)


async def example_multi_turn():
    """Example 3: Multi-turn conversation"""
    print("=" * 70)
    print("Example 3: Multi-turn Conversation")
    print("=" * 70)
    print()

    options = low_level_options()

    async with ClaudeSDKClient(options=options) as client:
        # Turn 1
        print("User: List training projects")
        print()
        await client.query("List training projects")
        async for msg in client.receive_response():
            display_message(msg)

        # Turn 2
        print("User: Now check MLflow service health")
        print()
        await client.query("Now check MLflow service health on port 5000")
        async for msg in client.receive_response():
            display_message(msg)


async def example_using_query_function():
    """Example 4: Using the simple query() function"""
    print("=" * 70)
    print("Example 4: Using Simple query() Function")
    print("=" * 70)
    print()

    from claude_agent_sdk import query
    options = low_level_options()

    print("User: What training projects exist?")
    print()

    # Simplest way - just use query()
    async for msg in query("What training projects exist?", options=options):
        display_message(msg)


async def main():
    """Run all examples"""
    print("\n" + "=" * 70)
    print("Galatea Agent - Low-level Claude SDK Examples")
    print("=" * 70)
    print()
    print("These examples explain the SDK layer beneath GalateaRuntime.")
    print("Use GalateaRuntime for application and production entry points.")
    print("\n")

    await example_basic_query()
    await example_with_agent_definition()
    await example_multi_turn()
    await example_using_query_function()

    print("=" * 70)
    print("All examples completed!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
