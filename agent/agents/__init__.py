"""
Agent definition and registry framework.

Provides agent configuration structure and discovery.

Key components:
- AgentDefinition: Agent configuration (tools, model, permissions)
- AgentMetadata: Runtime metadata tracking
- AgentRegistry: Agent discovery and management
- Predefined agents: Inspection, data, training, inference agents

Reference: Claude SDK's AgentDefinition and agent patterns.
"""

from agent.agents.definition import (
    AgentDefinition,
    AgentMetadata,
    PermissionMode,
    MemoryScope,
    EffortLevel,
    # Predefined agents
    INSPECTION_AGENT,
    DATA_AGENT,
    TRAINING_AGENT,
    INFERENCE_AGENT,
)
from agent.agents.registry import (
    AgentRegistry,
    get_registry,
    register_agent,
    get_agent,
)

__all__ = [
    # Definition
    "AgentDefinition",
    "AgentMetadata",
    "PermissionMode",
    "MemoryScope",
    "EffortLevel",
    # Predefined agents
    "INSPECTION_AGENT",
    "DATA_AGENT",
    "TRAINING_AGENT",
    "INFERENCE_AGENT",
    # Registry
    "AgentRegistry",
    "get_registry",
    "register_agent",
    "get_agent",
]
