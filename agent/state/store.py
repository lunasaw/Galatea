"""
Session state management for Galatea agents.

Provides abstractions for storing and retrieving agent session state,
transcript storage, and session resumption.

Reference: Claude SDK's session_store.py and session management system.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, List
from datetime import datetime


class SessionStore(ABC):
    """
    Abstract interface for agent session storage.

    Implementations can use memory, filesystem, Redis, S3, Postgres, etc.
    Reference: Claude SDK's SessionStore protocol.
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


class MemorySessionStore(SessionStore):
    """
    In-memory session store for development and testing.

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
            "transcript": transcript,
            "metadata": metadata,
            "updated_at": datetime.utcnow().isoformat(),
        }

    async def load_session(
        self,
        session_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Load session from memory."""
        return self._sessions.get(session_id)

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

    Reference: Claude SDK's session management patterns.
    """

    def __init__(self, store: SessionStore):
        """
        Initialize session manager.

        Args:
            store: SessionStore implementation
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

        Raises:
            NotImplementedError: Future: Stage 2+
        """
        raise NotImplementedError("Future: Stage 2+ - Session creation")

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

        Raises:
            NotImplementedError: Future: Stage 2+
        """
        raise NotImplementedError("Future: Stage 2+ - Session resumption")

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

        Raises:
            NotImplementedError: Future: Stage 2+
        """
        raise NotImplementedError("Future: Stage 2+ - Session forking")
