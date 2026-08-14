"""SDK-native agent registry for discovery and management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from claude_agent_sdk import AgentDefinition


@dataclass(frozen=True)
class AgentRecord:
    """Named SDK AgentDefinition plus optional registry metadata."""

    name: str
    definition: AgentDefinition
    tags: tuple[str, ...] = ()


class AgentRegistry:
    """Registry for Claude SDK AgentDefinition objects."""

    def __init__(self) -> None:
        self._agents: Dict[str, AgentRecord] = {}

    def register(
        self,
        name: str,
        definition: AgentDefinition,
        *,
        tags: Optional[List[str]] = None,
    ) -> None:
        """Register an SDK AgentDefinition under a stable runtime name."""
        if not name:
            raise ValueError("Agent name is required")
        if not isinstance(definition, AgentDefinition):
            raise TypeError("AgentRegistry only accepts claude_agent_sdk.AgentDefinition")
        if definition.permissionMode == "bypassPermissions":
            raise ValueError("Galatea agents must not use bypassPermissions")
        if name in self._agents:
            raise ValueError(f"Agent already registered: {name}")
        self._agents[name] = AgentRecord(name=name, definition=definition, tags=tuple(tags or ()))

    def unregister(self, name: str) -> None:
        if name not in self._agents:
            raise KeyError(f"Agent not found: {name}")
        del self._agents[name]

    def get(self, name: str) -> Optional[AgentDefinition]:
        record = self._agents.get(name)
        return record.definition if record else None

    def get_record(self, name: str) -> Optional[AgentRecord]:
        """Return the named record including registry metadata."""
        return self._agents.get(name)

    def to_sdk_agents(self) -> Dict[str, AgentDefinition]:
        """Return the mapping expected by ClaudeAgentOptions.agents."""
        return {name: record.definition for name, record in self._agents.items()}

    def list(self, filter_tags: Optional[List[str]] = None) -> List[str]:
        if not filter_tags:
            return sorted(self._agents)
        requested = set(filter_tags)
        return sorted(
            name
            for name, record in self._agents.items()
            if requested.issubset(record.tags)
        )

    def list_by_capability(self, capability: str) -> List[str]:
        return sorted(
            name
            for name, record in self._agents.items()
            if _has_capability(record, capability)
        )


_global_registry = AgentRegistry()


def get_registry() -> AgentRegistry:
    """Get global agent registry."""
    return _global_registry


def register_agent(
    name: str,
    definition: AgentDefinition,
    *,
    tags: Optional[List[str]] = None,
) -> None:
    """Register an SDK AgentDefinition in the global registry."""
    _global_registry.register(name, definition, tags=tags)


def get_agent(name: str) -> Optional[AgentDefinition]:
    """Get an SDK AgentDefinition from the global registry."""
    return _global_registry.get(name)


def _has_capability(record: AgentRecord, capability: str) -> bool:
    definition = record.definition
    haystack = [
        record.name,
        definition.description,
        definition.prompt,
        *(definition.tools or []),
        *(definition.skills or []),
        *record.tags,
    ]
    needle = capability.lower()
    return any(needle in item.lower() for item in haystack)
