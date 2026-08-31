"""
Galatea MCP Tools

MCP (Model Context Protocol) tools for Galatea platform operations.

Usage with Claude SDK:
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    from agent.tools import create_galatea_mcp_server

    options = ClaudeAgentOptions(
        mcp_servers={"galatea": create_galatea_mcp_server()},
    )

    async with ClaudeSDKClient(options) as client:
        await client.query("List training projects")
        async for msg in client.receive_response():
            # Handle messages

Available Tools (Stage 1):
    - list_training_projects: List all training projects
    - inspect_project_structure: Inspect project files
    - check_service_health: Check service status
    - inspect_mlflow_experiment: Check MLflow experiments
    - inspect_ray_status: Check Ray cluster

Future Tools (Stage 2+):
    - Ray Data tools
    - Ray Train tools
    - MLflow artifact tools
    - Quality validation tools
"""

from agent.tools.server import create_galatea_mcp_server

__all__ = [
    "create_galatea_mcp_server",
]
