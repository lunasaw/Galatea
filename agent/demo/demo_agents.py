#!/usr/bin/env python3
"""
Demo: Using Agent Definitions

Low-level demonstration of pre-defined SDK AgentDefinition objects.

Use ``GalateaRuntime`` for application and production entry points. This demo
keeps direct SDK usage only to show how the underlying definitions are wired.

Shows:
1. Using a single agent
2. Using multiple agents
3. Switching between agents in conversation
4. Agent-specific tools and prompts

Usage:
    python agent/demo/demo_agents.py
"""

import asyncio
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)

from agent.demo.demo_sdk_direct import low_level_options


def display_message(msg, show_tools=False):
    """Display message content"""
    if isinstance(msg, SystemMessage):
        return

    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if type(block).__name__ == 'ThinkingBlock':
                continue

            if isinstance(block, TextBlock):
                print(f"Claude: {block.text}")
            elif isinstance(block, ToolUseBlock) and show_tools:
                print(f"  🔧 [Tool: {block.name}]")

    elif isinstance(msg, ResultMessage):
        if msg.total_cost_usd:
            print(f"💰 ${msg.total_cost_usd:.4f}")
        print()


async def example_single_agent():
    """Example 1: Using a single agent"""
    print("=" * 70)
    print("Example 1: Using PLATFORM_INSPECTOR Agent")
    print("=" * 70)
    print()

    from agent.agents import PLATFORM_INSPECTOR

    options = low_level_options(
        agents={"inspector": PLATFORM_INSPECTOR},
    )

    async with ClaudeSDKClient(options) as client:
        print("User: Use inspector to check platform health")
        print()

        await client.query("Use the inspector agent to check platform health")

        async for msg in client.receive_response():
            display_message(msg, show_tools=True)


async def example_multiple_agents():
    """Example 2: Using multiple agents"""
    print("=" * 70)
    print("Example 2: Multiple Agents (Inspector + Analyzer)")
    print("=" * 70)
    print()

    from agent.agents import PLATFORM_INSPECTOR, EXPERIMENT_ANALYZER

    options = low_level_options(
        agents={
            "inspector": PLATFORM_INSPECTOR,
            "analyzer": EXPERIMENT_ANALYZER,
        },
    )

    async with ClaudeSDKClient(options) as client:
        print("User: Use inspector to list projects, then use analyzer to check experiments")
        print()

        await client.query(
            "First use inspector to list training projects. "
            "Then use analyzer to check MLflow experiments."
        )

        async for msg in client.receive_response():
            display_message(msg, show_tools=True)


async def example_agent_switching():
    """Example 3: Switching between agents in multi-turn conversation"""
    print("=" * 70)
    print("Example 3: Multi-turn with Agent Switching")
    print("=" * 70)
    print()

    from agent.agents import PLATFORM_INSPECTOR, DOCUMENTATION_GENERATOR

    options = low_level_options(
        agents={
            "inspector": PLATFORM_INSPECTOR,
            "docs": DOCUMENTATION_GENERATOR,
        },
    )

    async with ClaudeSDKClient(options) as client:
        # Turn 1: Use inspector
        print("User: Use inspector to list projects")
        print()
        await client.query("Use inspector to list training projects")

        async for msg in client.receive_response():
            display_message(msg)

        # Turn 2: Use documentation generator
        print("User: Use docs generator to explain the first project")
        print()
        await client.query("Use docs agent to explain the structure of the first project")

        async for msg in client.receive_response():
            display_message(msg)


async def example_all_stage_agents():
    """Example 4: All stage agents together"""
    print("=" * 70)
    print("Example 4: All Stage Agents (Data, Training, Inference)")
    print("=" * 70)
    print()

    from agent.agents import (
        DATA_PREPARER,
        TRAINING_ORCHESTRATOR,
        MODEL_EVALUATOR,
    )

    options = low_level_options(
        agents={
            "data": DATA_PREPARER,
            "training": TRAINING_ORCHESTRATOR,
            "inference": MODEL_EVALUATOR,
        },
    )

    async with ClaudeSDKClient(options) as client:
        print("User: Explain what each stage agent does")
        print()

        await client.query(
            "Explain what the data, training, and inference agents are designed to do. "
            "What are their responsibilities?"
        )

        async for msg in client.receive_response():
            display_message(msg)


async def main():
    """Run all examples"""
    print("\n" + "=" * 70)
    print("Galatea Agent Definitions - Examples")
    print("=" * 70)
    print()
    print("These low-level examples demonstrate SDK AgentDefinition wiring:")
    print("- Using pre-defined AgentDefinition objects")
    print("- Combining multiple agents")
    print("- Switching between agents")
    print("\n")

    await example_single_agent()
    await example_multiple_agents()
    await example_agent_switching()
    await example_all_stage_agents()

    print("=" * 70)
    print("All examples completed!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
