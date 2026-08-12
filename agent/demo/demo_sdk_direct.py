#!/usr/bin/env python3
"""
Direct Claude SDK Usage with Galatea Tools

展示如何直接使用 Claude SDK + Galatea MCP 工具，不经过 GalateaRuntime 封装。
这是最灵活、最符合 SDK 最佳实践的方式。

Usage:
    python agent/demo/demo_sdk_direct.py
"""

import asyncio
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

    # Import Galatea MCP server
    from agent.tools.server import create_galatea_mcp_server

    # Create options with Galatea tools
    options = ClaudeAgentOptions(
        model="claude-opus-5",
        mcp_servers={"galatea-platform": create_galatea_mcp_server()},
        permission_mode="dontAsk",  # Auto-approve read-only tools
        cwd=Path.cwd(),
    )

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

    from claude_agent_sdk import AgentDefinition
    from agent.tools.server import create_galatea_mcp_server

    # Define a specialized agent
    options = ClaudeAgentOptions(
        model="claude-opus-5",
        mcp_servers={"galatea-platform": create_galatea_mcp_server()},
        agents={
            "platform-inspector": AgentDefinition(
                description="Inspects Galatea platform health and status",
                prompt="You are a platform inspector. Check service health, list projects, "
                       "and report status clearly and concisely.",
                tools=["list_training_projects", "check_service_health", "inspect_ray_status"],
                model="sonnet",  # Use faster model for inspection
            )
        },
        permission_mode="dontAsk",
        cwd=Path.cwd(),
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

    from agent.tools.server import create_galatea_mcp_server

    options = ClaudeAgentOptions(
        model="claude-opus-5",
        mcp_servers={"galatea-platform": create_galatea_mcp_server()},
        permission_mode="dontAsk",
        cwd=Path.cwd(),
    )

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
    from agent.tools.server import create_galatea_mcp_server

    options = ClaudeAgentOptions(
        mcp_servers={"galatea-platform": create_galatea_mcp_server()},
        permission_mode="dontAsk",
        cwd=Path.cwd(),
    )

    print("User: What training projects exist?")
    print()

    # Simplest way - just use query()
    async for msg in query("What training projects exist?", options=options):
        display_message(msg)


async def main():
    """Run all examples"""
    print("\n" + "=" * 70)
    print("Galatea Agent - Direct Claude SDK Usage Examples")
    print("=" * 70)
    print()
    print("These examples show how to use Claude SDK directly with Galatea tools,")
    print("following SDK best practices without the GalateaRuntime wrapper.")
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
