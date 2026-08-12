"""
Agent Definitions

Custom agent definitions for Galatea platform:
- trainer: Execute training jobs with platform contracts
- tuner: Orchestrate hyperparameter tuning
- analyzer: Analyze experiments and recommend optimizations
- reviewer: Review models for quality and compliance
- deployer: Manage model deployment and promotion
"""

from pathlib import Path
from claude_agent_sdk import AgentDefinition
from typing import Dict


def load_agent_definitions() -> Dict[str, AgentDefinition]:
    """Load agent definitions for Galatea platform."""
    # To be implemented
    pass


__all__ = ["load_agent_definitions"]
