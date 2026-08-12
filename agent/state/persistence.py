"""
State persistence utilities.

Helper functions for serializing and deserializing agent state.
"""

import json
from pathlib import Path
from typing import Any, Dict


async def save_to_file(
    data: Dict[str, Any],
    file_path: Path,
) -> None:
    """
    Save state dictionary to JSON file.

    Args:
        data: State data to save
        file_path: Target file path

    Raises:
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - File persistence")


async def load_from_file(
    file_path: Path,
) -> Dict[str, Any]:
    """
    Load state dictionary from JSON file.

    Args:
        file_path: Source file path

    Returns:
        State data dictionary

    Raises:
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - File loading")


def serialize_state(state: Any) -> str:
    """
    Serialize state object to JSON string.

    Args:
        state: State object with to_dict() method

    Returns:
        JSON string

    Raises:
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - Serialization")


def deserialize_state(
    json_str: str,
    state_class: type,
) -> Any:
    """
    Deserialize JSON string to state object.

    Args:
        json_str: JSON string
        state_class: State class with from_dict() method

    Returns:
        State object instance

    Raises:
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - Deserialization")
