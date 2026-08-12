"""
Utility functions for Galatea agents.

Provides error handling, logging, and validation utilities.

Key components:
- Errors: Structured exception types
- Logging: Structured and audit logging
- Validation: Input validation helpers
"""

from agent.utils.errors import (
    GalateaAgentError,
    StateError,
    SessionNotFoundError,
    ExperimentNotFoundError,
    WorkflowError,
    InvalidTransitionError,
    WorkflowNotFoundError,
    StageExecutionError,
    ToolError,
    ToolNotFoundError,
    ToolExecutionError,
    ConfigError,
    ConfigValidationError,
    ConfigNotFoundError,
    IntegrationError,
    MLflowError,
    RayError,
    MinIOError,
)

__all__ = [
    # Base
    "GalateaAgentError",
    # State
    "StateError",
    "SessionNotFoundError",
    "ExperimentNotFoundError",
    # Workflow
    "WorkflowError",
    "InvalidTransitionError",
    "WorkflowNotFoundError",
    "StageExecutionError",
    # Tool
    "ToolError",
    "ToolNotFoundError",
    "ToolExecutionError",
    # Config
    "ConfigError",
    "ConfigValidationError",
    "ConfigNotFoundError",
    # Integration
    "IntegrationError",
    "MLflowError",
    "RayError",
    "MinIOError",
]
