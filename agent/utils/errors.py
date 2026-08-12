"""
Custom exceptions for Galatea agents.

Provides structured error types for different failure modes.
"""


class GalateaAgentError(Exception):
    """Base exception for all Galatea agent errors."""

    pass


# State management errors
class StateError(GalateaAgentError):
    """Base class for state management errors."""

    pass


class SessionNotFoundError(StateError):
    """Raised when session is not found."""

    def __init__(self, session_id: str):
        """
        Initialize session not found error.

        Args:
            session_id: Session ID that was not found
        """
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class ExperimentNotFoundError(StateError):
    """Raised when experiment is not found."""

    def __init__(self, experiment_id: str):
        """
        Initialize experiment not found error.

        Args:
            experiment_id: Experiment ID that was not found
        """
        self.experiment_id = experiment_id
        super().__init__(f"Experiment not found: {experiment_id}")


# Workflow errors
class WorkflowError(GalateaAgentError):
    """Base class for workflow errors."""

    pass


class InvalidTransitionError(WorkflowError):
    """Raised when workflow transition is invalid."""

    def __init__(self, from_stage: str, to_stage: str, reason: str):
        """
        Initialize invalid transition error.

        Args:
            from_stage: Current stage
            to_stage: Target stage
            reason: Reason why transition is invalid
        """
        self.from_stage = from_stage
        self.to_stage = to_stage
        self.reason = reason
        super().__init__(
            f"Invalid transition from {from_stage} to {to_stage}: {reason}"
        )


class WorkflowNotFoundError(WorkflowError):
    """Raised when workflow is not found."""

    def __init__(self, workflow_id: str):
        """
        Initialize workflow not found error.

        Args:
            workflow_id: Workflow ID that was not found
        """
        self.workflow_id = workflow_id
        super().__init__(f"Workflow not found: {workflow_id}")


class StageExecutionError(WorkflowError):
    """Raised when stage execution fails."""

    def __init__(self, stage: str, reason: str):
        """
        Initialize stage execution error.

        Args:
            stage: Stage that failed
            reason: Failure reason
        """
        self.stage = stage
        self.reason = reason
        super().__init__(f"Stage '{stage}' execution failed: {reason}")


# Tool errors
class ToolError(GalateaAgentError):
    """Base class for tool errors."""

    pass


class ToolNotFoundError(ToolError):
    """Raised when tool is not found."""

    def __init__(self, tool_name: str):
        """
        Initialize tool not found error.

        Args:
            tool_name: Tool name that was not found
        """
        self.tool_name = tool_name
        super().__init__(f"Tool not found: {tool_name}")


class ToolExecutionError(ToolError):
    """Raised when tool execution fails."""

    def __init__(self, tool_name: str, reason: str):
        """
        Initialize tool execution error.

        Args:
            tool_name: Tool that failed
            reason: Failure reason
        """
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"Tool '{tool_name}' execution failed: {reason}")


# Configuration errors
class ConfigError(GalateaAgentError):
    """Base class for configuration errors."""

    pass


class ConfigValidationError(ConfigError):
    """Raised when configuration is invalid."""

    def __init__(self, field: str, reason: str):
        """
        Initialize config validation error.

        Args:
            field: Configuration field that is invalid
            reason: Validation failure reason
        """
        self.field = field
        self.reason = reason
        super().__init__(f"Invalid config field '{field}': {reason}")


class ConfigNotFoundError(ConfigError):
    """Raised when configuration is not found."""

    def __init__(self, config_path: str):
        """
        Initialize config not found error.

        Args:
            config_path: Configuration path that was not found
        """
        self.config_path = config_path
        super().__init__(f"Configuration not found: {config_path}")


# Integration errors
class IntegrationError(GalateaAgentError):
    """Base class for platform integration errors."""

    pass


class MLflowError(IntegrationError):
    """Raised when MLflow operation fails."""

    pass


class RayError(IntegrationError):
    """Raised when Ray operation fails."""

    pass


class MinIOError(IntegrationError):
    """Raised when MinIO operation fails."""

    pass
