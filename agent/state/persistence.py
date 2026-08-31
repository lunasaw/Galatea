"""State persistence utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


async def save_to_file(data: Dict[str, Any], file_path: Path) -> None:
    """Save a state dictionary to JSON."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    tmp_path.replace(file_path)


async def load_from_file(file_path: Path) -> Dict[str, Any]:
    """Load a state dictionary from JSON."""
    if not file_path.exists():
        raise FileNotFoundError(file_path)
    return json.loads(file_path.read_text(encoding="utf-8"))


def serialize_state(state: Any) -> str:
    """Serialize an object with to_dict() to JSON."""
    if hasattr(state, "to_dict"):
        state = state.to_dict()
    return json.dumps(state, ensure_ascii=False, sort_keys=True, default=str)


def deserialize_state(json_str: str, state_class: type) -> Any:
    """Deserialize JSON into a state object."""
    data = json.loads(json_str)
    if hasattr(state_class, "from_dict"):
        return state_class.from_dict(data)
    return state_class(**data)
