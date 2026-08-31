"""
State management for Galatea agents.

Provides session storage, experiment tracking, and state persistence.

Key components:
- AgentStateStore: Abstract interface for Galatea application session state
- InMemoryAgentStateStore: In-memory implementation
- SessionManager: High-level session management
- ExperimentState: Experiment workflow state tracking
- Persistence utilities: Save/load helpers
"""

from agent.state.store import (
    AgentStateStore,
    InMemoryAgentStateStore,
    SessionStore,
    MemorySessionStore,
    SessionManager,
)
from agent.state.experiment import (
    ExperimentState,
    ExperimentStage,
    ExperimentStateManager,
)

__all__ = [
    "AgentStateStore",
    "InMemoryAgentStateStore",
    "SessionStore",
    "MemorySessionStore",
    "SessionManager",
    "ExperimentState",
    "ExperimentStage",
    "ExperimentStateManager",
]
