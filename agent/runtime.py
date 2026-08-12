"""
Galatea Agent Runtime

Wraps ClaudeSDKClient with platform-specific configuration:
- In-process MCP server with Galatea tools
- Structured output schema validation
- Session management
- Permission controls
"""

import os
from pathlib import Path
from typing import Optional, Dict, Any, AsyncIterator
from datetime import datetime

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, UserMessage

from agent.tools.server import create_galatea_mcp_server


class GalateaRuntime:
    """
    Runtime wrapper for Galatea agent operations.

    Manages Claude SDK client lifecycle, tool registration,
    and structured output validation.
    """

    def __init__(
        self,
        project_root: Path,
        mlflow_tracking_uri: str = "http://127.0.0.1:5000",
        model: str = "claude-opus-4-20250514",
    ):
        """
        Initialize Galatea runtime.

        Args:
            project_root: Root directory of Galatea platform
            mlflow_tracking_uri: MLflow tracking server URI
            model: Claude model to use
        """
        self.project_root = project_root
        self.mlflow_uri = mlflow_tracking_uri
        self.model = model

        # Create MCP server with inspection tools
        self.mcp_server = create_galatea_mcp_server()

        # Claude SDK client (initialized in __aenter__)
        self._client: Optional[ClaudeSDKClient] = None

    async def __aenter__(self):
        """Enter async context manager."""
        # Create Claude SDK options
        options = ClaudeAgentOptions(
            model=self.model,
            mcp_servers={"galatea-platform": self.mcp_server},
            permission_mode="dontAsk",  # Stage 1: read-only tools only
            cwd=self.project_root,
        )

        # Initialize Claude SDK client
        self._client = ClaudeSDKClient(options)
        await self._client.__aenter__()

        return self

    async def __aexit__(self, *args):
        """Exit async context manager."""
        if self._client:
            await self._client.__aexit__(*args)

    async def query(
        self,
        prompt: str,
        output_schema: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[Any]:
        """
        Execute a query and stream response messages.

        Args:
            prompt: Query prompt
            output_schema: Optional JSON schema for structured output

        Yields:
            Response messages from agent
        """
        if not self._client:
            raise RuntimeError("Runtime not initialized. Use 'async with' context.")

        # Add structured output request if schema provided
        if output_schema:
            schema_instruction = (
                f"\n\nIMPORTANT: Return your response as structured JSON "
                f"matching this schema:\n{output_schema}"
            )
            prompt = prompt + schema_instruction

        # Send query
        await self._client.query(prompt)

        # Stream response
        async for message in self._client.receive_response():
            yield message

    async def inspect_platform(self) -> Dict[str, Any]:
        """
        Inspect Galatea platform status using agent.

        Returns:
            Platform inspection results
        """
        prompt = f"""Inspect the Galatea ML training platform at {self.project_root}.

Please use the available inspection tools to check:
1. List all training projects in train-model/
2. Check health of key services: mlflow (port 5000), minio (port 9000)
3. Check Ray cluster status
4. For the 'ray-cats-and-dogs' project, inspect its structure

Summarize your findings in a clear report."""

        messages = []
        async for message in self.query(prompt):
            messages.append(message)

        # Extract final text response
        if messages:
            last_message = messages[-1]
            return {
                "status": "success",
                "response": last_message.content if hasattr(last_message, 'content') else str(last_message),
                "timestamp": datetime.utcnow().isoformat(),
            }

        return {
            "status": "failed",
            "error": "No response received",
            "timestamp": datetime.utcnow().isoformat(),
        }
