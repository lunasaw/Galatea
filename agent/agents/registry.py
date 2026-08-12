"""
Agent registry for discovery and management.

Manages agent definitions and provides lookup capabilities.
"""

from typing import Dict, List, Optional
from agent.agents.definition import AgentDefinition


class AgentRegistry:
    """
    Registry for agent definitions.

    Provides registration, lookup, and listing of agents.
    """

    def __init__(self):
        """Initialize empty registry."""
        self._agents: Dict[str, AgentDefinition] = {}

    def register(self, definition: AgentDefinition) -> None:
        """
        Register agent definition.

        Args:
            definition: Agent definition to register

        Raises:
            ValueError: If agent name already exists
            NotImplementedError: Future: Stage 2+
        """
        raise NotImplementedError("Future: Stage 2+ - Agent registration")

    def unregister(self, name: str) -> None:
        """
        Unregister agent definition.

        Args:
            name: Agent name to remove

        Raises:
            KeyError: If agent not found
            NotImplementedError: Future: Stage 2+
        """
        raise NotImplementedError("Future: Stage 2+ - Agent unregistration")

    def get(self, name: str) -> Optional[AgentDefinition]:
        """
        Get agent definition by name.

        Args:
            name: Agent name

        Returns:
            AgentDefinition or None

        Raises:
            NotImplementedError: Future: Stage 2+
        """
        raise NotImplementedError("Future: Stage 2+ - Agent lookup")

    def list(self, filter_tags: Optional[List[str]] = None) -> List[str]:
        """
        List registered agent names.

        Args:
            filter_tags: Optional tags to filter by

        Returns:
            List of agent names

        Raises:
            NotImplementedError: Future: Stage 2+
        """
        raise NotImplementedError("Future: Stage 2+ - Agent listing")

    def list_by_capability(self, capability: str) -> List[str]:
        """
        List agents with specific capability.

        Args:
            capability: Capability name (e.g., "ray_data", "mlflow_tracking")

        Returns:
            List of agent names

        Raises:
            NotImplementedError: Future: Stage 2+
        """
        raise NotImplementedError("Future: Stage 2+ - Capability-based listing")


# Global registry instance
_global_registry = AgentRegistry()


def get_registry() -> AgentRegistry:
    """
    Get global agent registry.

    Returns:
        Global AgentRegistry instance
    """
    return _global_registry


def register_agent(definition: AgentDefinition) -> None:
    """
    Register agent in global registry.

    Args:
        definition: Agent definition to register

    Raises:
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - Global registration")


def get_agent(name: str) -> Optional[AgentDefinition]:
    """
    Get agent from global registry.

    Args:
        name: Agent name

    Returns:
        AgentDefinition or None

    Raises:
        NotImplementedError: Future: Stage 2+
    """
    raise NotImplementedError("Future: Stage 2+ - Global lookup")
