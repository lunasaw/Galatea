"""
Configuration Management

Agent and platform configuration:
- agents.yaml: Agent definitions
- tools.yaml: Tool configurations
- platform.yaml: Platform settings
"""

from pathlib import Path
import yaml
from typing import Dict, Any


def load_config(config_name: str) -> Dict[str, Any]:
    """Load configuration from YAML file."""
    config_path = Path(__file__).parent / f"{config_name}.yaml"
    if not config_path.exists():
        return {}

    with open(config_path, "r") as f:
        return yaml.safe_load(f)


__all__ = ["load_config"]
