"""
State management for Galatea agents.

Provides session storage, experiment tracking, and state persistence.

Key components:
- SessionStore: Abstract interface for session storage
- MemorySessionStore: In-memory implementation
- SessionManager: High-level session management
- ExperimentState: Experiment workflow state tracking
- Persistence utilities: Save/load helpers
"""

from agent.state.store import (
    SessionStore,
    MemorySessionStore,
    SessionManager,
)
from agent.state.experiment import (
    ExperimentState,
    ExperimentStage,
    ExperimentStateManager,
)
from agent.state.patrol import (
    FilePatrolSessionStore,
    PatrolSession,
    new_patrol_session,
)

__all__ = [
    "SessionStore",
    "MemorySessionStore",
    "SessionManager",
    "ExperimentState",
    "ExperimentStage",
    "ExperimentStateManager",
    "FilePatrolSessionStore",
    "PatrolSession",
    "new_patrol_session",
]
