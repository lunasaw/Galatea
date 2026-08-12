"""Input validation utilities for Galatea agents."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
METRIC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,191}$")
MLFLOW_RUN_RE = re.compile(r"^[A-Fa-f0-9]{32}$|^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")
SENSITIVE_KEYS = ("key", "token", "secret", "password", "credential")


def validate_project_name(project_name: str) -> bool:
    if not isinstance(project_name, str) or not PROJECT_RE.match(project_name):
        raise ValueError(f"Invalid project name: {project_name!r}")
    if project_name in {".", ".."} or "/" in project_name or "\\" in project_name:
        raise ValueError("Project name must not contain path separators")
    return True


def validate_session_id(session_id: str) -> bool:
    if not isinstance(session_id, str) or not SESSION_RE.match(session_id):
        raise ValueError(f"Invalid session ID: {session_id!r}")
    return True


def validate_uri(uri: str, schemes: Optional[List[str]] = None) -> bool:
    if not isinstance(uri, str) or not uri:
        raise ValueError("URI must be a non-empty string")
    parsed = urlparse(uri)
    if schemes and parsed.scheme not in schemes:
        raise ValueError(f"URI scheme {parsed.scheme!r} is not allowed")
    if parsed.scheme and not (parsed.netloc or parsed.path):
        raise ValueError(f"URI is missing location: {uri}")
    return True


def validate_file_path(
    path: Path,
    must_exist: bool = False,
    must_be_file: bool = False,
    must_be_dir: bool = False,
) -> bool:
    if not isinstance(path, Path):
        path = Path(path)
    if must_exist and not path.exists():
        raise ValueError(f"Path does not exist: {path}")
    if must_be_file and not path.is_file():
        raise ValueError(f"Path is not a file: {path}")
    if must_be_dir and not path.is_dir():
        raise ValueError(f"Path is not a directory: {path}")
    if must_be_file and must_be_dir:
        raise ValueError("Path cannot be required to be both file and directory")
    return True


def validate_config(
    config: Dict[str, Any],
    required_fields: List[str],
    field_types: Optional[Dict[str, type]] = None,
) -> bool:
    if not isinstance(config, dict):
        raise ValueError("Config must be a dictionary")
    missing = [field for field in required_fields if field not in config]
    if missing:
        raise ValueError(f"Missing required config fields: {', '.join(missing)}")
    for field, expected_type in (field_types or {}).items():
        if field in config and not isinstance(config[field], expected_type):
            raise ValueError(
                f"Config field {field!r} must be {expected_type.__name__}, got {type(config[field]).__name__}"
            )
    return True


def validate_metric_name(metric_name: str) -> bool:
    if not isinstance(metric_name, str) or not METRIC_RE.match(metric_name):
        raise ValueError(f"Invalid metric name: {metric_name!r}")
    return True


def validate_mlflow_run_id(run_id: str) -> bool:
    if not isinstance(run_id, str) or not MLFLOW_RUN_RE.match(run_id):
        raise ValueError(f"Invalid MLflow run ID: {run_id!r}")
    return True


def sanitize_tool_input(tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(tool_input, dict):
        raise ValueError(f"{tool_name} input must be a dictionary")
    sanitized = {}
    for key, value in tool_input.items():
        if any(marker in key.lower() for marker in SENSITIVE_KEYS):
            sanitized[key] = "***"
        elif isinstance(value, dict):
            sanitized[key] = sanitize_tool_input(tool_name, value)
        elif isinstance(value, list):
            sanitized[key] = [
                sanitize_tool_input(tool_name, item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            sanitized[key] = value
    return sanitized
