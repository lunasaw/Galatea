"""Application state management for Galatea agents.

This module stores Galatea workflow/session metadata. It is intentionally
separate from the Claude SDK ``SessionStore`` transcript mirror protocol,
which requires ``append`` and ``load`` methods and is passed directly to
``ClaudeAgentOptions.session_store``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
import copy
from typing import Any, Dict, List, Optional


class AgentStateStore(ABC):
    """
    Abstract interface for Galatea application session state.

    Implementations can use memory, filesystem, Redis, S3, Postgres, etc. This
    is not Claude SDK's SessionStore transcript protocol.
    """

    @abstractmethod
    async def save_session(
        self,
        session_id: str,
        transcript: List[Dict[str, Any]],
        metadata: Dict[str, Any],
    ) -> None:
        """
        Save session state.

        Args:
            session_id: Unique session identifier
            transcript: Conversation transcript (messages)
            metadata: Session metadata (start_time, agent_type, project_name, etc.)

        Raises:
            NotImplementedError: Future: Stage 2+ implementations
        """
        raise NotImplementedError("Future: Stage 2+ - Session persistence")

    @abstractmethod
    async def load_session(
        self,
        session_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Load session state by ID.

        Args:
            session_id: Session identifier

        Returns:
            Session data with transcript and metadata, or None if not found

        Raises:
            NotImplementedError: Future: Stage 2+ implementations
        """
        raise NotImplementedError("Future: Stage 2+ - Session loading")

    @abstractmethod
    async def delete_session(
        self,
        session_id: str,
    ) -> bool:
        """
        Delete session state.

        Args:
            session_id: Session identifier

        Returns:
            True if deleted, False if not found

        Raises:
            NotImplementedError: Future: Stage 2+ implementations
        """
        raise NotImplementedError("Future: Stage 2+ - Session deletion")

    @abstractmethod
    async def list_sessions(
        self,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """
        List session IDs matching filter criteria.

        Args:
            filter_metadata: Optional metadata filters (e.g., project_name, agent_type)

        Returns:
            List of session IDs

        Raises:
            NotImplementedError: Future: Stage 2+ implementations
        """
        raise NotImplementedError("Future: Stage 2+ - Session listing")


class InMemoryAgentStateStore(AgentStateStore):
    """
    In-memory Galatea application state store for development and testing.

    Sessions are lost when process exits.
    """

    def __init__(self):
        """Initialize in-memory storage."""
        self._sessions: Dict[str, Dict[str, Any]] = {}

    async def save_session(
        self,
        session_id: str,
        transcript: List[Dict[str, Any]],
        metadata: Dict[str, Any],
    ) -> None:
        """Save session to memory."""
        self._sessions[session_id] = {
            "session_id": session_id,
            "transcript": copy.deepcopy(transcript),
            "metadata": copy.deepcopy(metadata),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def load_session(
        self,
        session_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Load session from memory."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        return copy.deepcopy(session)

    async def delete_session(
        self,
        session_id: str,
    ) -> bool:
        """Delete session from memory."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    async def list_sessions(
        self,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """List sessions in memory."""
        if not filter_metadata:
            return list(self._sessions.keys())

        # Filter by metadata
        matching = []
        for session_id, data in self._sessions.items():
            metadata = data.get("metadata", {})
            if all(metadata.get(k) == v for k, v in filter_metadata.items()):
                matching.append(session_id)
        return matching


class SessionManager:
    """
    High-level session management with resume/fork support.

    This manages Galatea application state only. Use a Claude SDK SessionStore
    implementation for transcript resume/fork.
    """

    def __init__(self, store: AgentStateStore):
        """
        Initialize session manager.

        Args:
            store: AgentStateStore implementation
        """
        self.store = store

    async def create_session(
        self,
        session_id: str,
        agent_type: str,
        project_name: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Create new session.

        Args:
            session_id: Session identifier
            agent_type: Agent type (data, training, inference, coordinator)
            project_name: Training project name
            metadata: Additional metadata

        Returns:
            Session ID

        """
        existing = await self.store.load_session(session_id)
        if existing is not None:
            raise ValueError(f"Session already exists: {session_id}")
        data = {
            "agent_type": agent_type,
            "project_name": project_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **(metadata or {}),
        }
        await self.store.save_session(session_id, transcript=[], metadata=data)
        return session_id

    async def resume_session(
        self,
        session_id: str,
    ) -> Dict[str, Any]:
        """
        Resume existing session.

        Args:
            session_id: Session to resume

        Returns:
            Session state with transcript

        """
        session = await self.store.load_session(session_id)
        if session is None:
            raise KeyError(f"Session not found: {session_id}")
        return session

    async def fork_session(
        self,
        source_session_id: str,
        new_session_id: str,
    ) -> str:
        """
        Fork session from existing session.

        Args:
            source_session_id: Source session to fork from
            new_session_id: New session identifier

        Returns:
            New session ID

        """
        source = await self.resume_session(source_session_id)
        if await self.store.load_session(new_session_id) is not None:
            raise ValueError(f"Session already exists: {new_session_id}")
        metadata = copy.deepcopy(source.get("metadata", {}))
        metadata.update(
            {
                "forked_from": source_session_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        transcript = copy.deepcopy(source.get("transcript", []))
        await self.store.save_session(new_session_id, transcript=transcript, metadata=metadata)
        return new_session_id


# Backward-compatible aliases for older imports. Prefer the AgentStateStore
# names in new code to avoid confusion with claude_agent_sdk.SessionStore.
SessionStore = AgentStateStore
MemorySessionStore = InMemoryAgentStateStore
