"""
Input validation utilities for Galatea agents.

Provides validation helpers for agent inputs, tool parameters, and configurations.
"""

from typing import Any, Dict, List, Optional
from pathlib import Path


def validate_project_name(project_name: str) -> bool:
    """
    Validate project name format.

    Args:
        project_name: Project name to validate

    Returns:
        True if valid

    Raises:
        ValueError: If project name is invalid
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - Project name validation")


def validate_session_id(session_id: str) -> bool:
    """
    Validate session ID format.

    Args:
        session_id: Session ID to validate

    Returns:
        True if valid

    Raises:
        ValueError: If session ID is invalid
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - Session ID validation")


def validate_uri(uri: str, schemes: Optional[List[str]] = None) -> bool:
    """
    Validate URI format and scheme.

    Args:
        uri: URI to validate
        schemes: Optional list of allowed schemes (e.g., ["file", "s3", "mlflow-artifacts"])

    Returns:
        True if valid

    Raises:
        ValueError: If URI is invalid
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - URI validation")


def validate_file_path(
    path: Path,
    must_exist: bool = False,
    must_be_file: bool = False,
    must_be_dir: bool = False,
) -> bool:
    """
    Validate file path.

    Args:
        path: Path to validate
        must_exist: If True, path must exist
        must_be_file: If True, path must be a file
        must_be_dir: If True, path must be a directory

    Returns:
        True if valid

    Raises:
        ValueError: If path is invalid
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - File path validation")


def validate_config(
    config: Dict[str, Any],
    required_fields: List[str],
    field_types: Optional[Dict[str, type]] = None,
) -> bool:
    """
    Validate configuration dictionary.

    Args:
        config: Configuration to validate
        required_fields: Required field names
        field_types: Optional type requirements per field

    Returns:
        True if valid

    Raises:
        ValueError: If config is invalid
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - Config validation")


def validate_metric_name(metric_name: str) -> bool:
    """
    Validate metric name format.

    Args:
        metric_name: Metric name to validate

    Returns:
        True if valid

    Raises:
        ValueError: If metric name is invalid
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - Metric name validation")


def validate_mlflow_run_id(run_id: str) -> bool:
    """
    Validate MLflow run ID format.

    Args:
        run_id: Run ID to validate

    Returns:
        True if valid

    Raises:
        ValueError: If run ID is invalid
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - Run ID validation")


def sanitize_tool_input(
    tool_name: str,
    tool_input: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Sanitize tool input for security.

    Args:
        tool_name: Tool name
        tool_input: Tool input parameters

    Returns:
        Sanitized tool input

    Raises:
        ValueError: If input contains unsafe content
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - Input sanitization")
