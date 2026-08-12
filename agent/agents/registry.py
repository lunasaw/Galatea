"""Agent registry for discovery and management."""

from __future__ import annotations

from typing import Dict, List, Optional

from agent.agents.definition import AgentDefinition


class AgentRegistry:
    """Registry for Galatea agent definitions."""

    def __init__(self) -> None:
        self._agents: Dict[str, AgentDefinition] = {}

    def register(self, definition: AgentDefinition) -> None:
        definition.validate()
        if definition.name in self._agents:
            raise ValueError(f"Agent already registered: {definition.name}")
        self._agents[definition.name] = definition

    def unregister(self, name: str) -> None:
        if name not in self._agents:
            raise KeyError(f"Agent not found: {name}")
        del self._agents[name]

    def get(self, name: str) -> Optional[AgentDefinition]:
        return self._agents.get(name)

    def list(self, filter_tags: Optional[List[str]] = None) -> List[str]:
        if not filter_tags:
            return sorted(self._agents)
        return sorted(
            name
            for name, definition in self._agents.items()
            if definition.tools and all(tag in definition.tools for tag in filter_tags)
        )

    def list_by_capability(self, capability: str) -> List[str]:
        return sorted(
            name
            for name, definition in self._agents.items()
            if _has_capability(definition, capability)
        )


_global_registry = AgentRegistry()


def get_registry() -> AgentRegistry:
    """Get global agent registry."""
    return _global_registry


def register_agent(definition: AgentDefinition) -> None:
    """Register an agent in the global registry."""
    _global_registry.register(definition)


def get_agent(name: str) -> Optional[AgentDefinition]:
    """Get an agent from the global registry."""
    return _global_registry.get(name)


def _has_capability(definition: AgentDefinition, capability: str) -> bool:
    haystack = [
        definition.name,
        definition.description,
        definition.prompt,
        *(definition.tools or []),
        *(definition.skills or []),
    ]
    needle = capability.lower()
    return any(needle in item.lower() for item in haystack)
