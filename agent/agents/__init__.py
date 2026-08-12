"""
Galatea Agent Definitions

Pre-defined agents using Claude SDK's AgentDefinition pattern.

Usage:
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    from agent.tools import create_galatea_mcp_server
    from agent.agents import PLATFORM_INSPECTOR, DATA_PREPARER

    options = ClaudeAgentOptions(
        mcp_servers={"galatea": create_galatea_mcp_server()},
        agents={
            "inspector": PLATFORM_INSPECTOR,
            "data": DATA_PREPARER,
        },
    )

    async with ClaudeSDKClient(options) as client:
        await client.query("Use inspector to check platform health")
        async for msg in client.receive_response():
            # Handle messages
"""

from agent.agents.definitions import (
    # Platform management
    PLATFORM_INSPECTOR,
    # Stage agents
    DATA_PREPARER,
    TRAINING_ORCHESTRATOR,
    MODEL_EVALUATOR,
    # Utility agents
    EXPERIMENT_ANALYZER,
    DOCUMENTATION_GENERATOR,
)

__all__ = [
    # Platform management
    "PLATFORM_INSPECTOR",
    # Stage agents
    "DATA_PREPARER",
    "TRAINING_ORCHESTRATOR",
    "MODEL_EVALUATOR",
    # Utility agents
    "EXPERIMENT_ANALYZER",
    "DOCUMENTATION_GENERATOR",
]
