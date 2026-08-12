"""
Galatea Agent System

Python-based agent orchestration for the Galatea ML Training Platform.
Built on claude-agent-sdk with custom MCP tools for MLflow, Ray, and MinIO.

Architecture:
    - GalateaRuntime: Low-level runtime wrapping ClaudeSDKClient
    - GalateaAgentClient: High-level client for common operations
    - Custom MCP tools: MLflow, Ray, data validation, MinIO operations
    - Agent definitions: trainer, tuner, analyzer, reviewer, deployer
    - Workflows: Training, tuning, evaluation, optimization
    - State management: Session store, experiment state tracking
"""

from .client import GalateaAgentClient
from .runtime import GalateaRuntime

__all__ = ["GalateaAgentClient", "GalateaRuntime"]
__version__ = "0.1.0"
