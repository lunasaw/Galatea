"""Loop-shaped state machine for patrol-push agent rounds."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from agent.schemas.patrol import current_timestamp


class PatrolRunState(str, Enum):
    """States for one patrol round."""

    IDLE = "idle"
    COLLECT_CONTEXT = "collect_context"
    INSPECT = "inspect"
    CLASSIFY_FINDINGS = "classify_findings"
    RECOMMEND = "recommend"
    REQUEST_APPROVAL = "request_approval"
    PERSIST_STATE = "persist_state"
    SCHEDULE_NEXT = "schedule_next"
    INSPECT_FAILED = "inspect_failed"
    RETRY_LATER = "retry_later"
    DEGRADED_SUMMARY = "degraded_summary"
    NEEDS_HUMAN = "needs_human"


@dataclass
class PatrolTransition:
    """Transition validation result."""

    from_state: PatrolRunState
    to_state: PatrolRunState
    allowed: bool
    reason: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class PatrolRunStateMachine:
    """Deterministic state machine for a single patrol round."""

    transitions: Dict[PatrolRunState, List[PatrolRunState]] = {
        PatrolRunState.IDLE: [PatrolRunState.COLLECT_CONTEXT],
        PatrolRunState.COLLECT_CONTEXT: [PatrolRunState.INSPECT],
        PatrolRunState.INSPECT: [PatrolRunState.CLASSIFY_FINDINGS, PatrolRunState.INSPECT_FAILED],
        PatrolRunState.INSPECT_FAILED: [
            PatrolRunState.RETRY_LATER,
            PatrolRunState.DEGRADED_SUMMARY,
            PatrolRunState.NEEDS_HUMAN,
        ],
        PatrolRunState.RETRY_LATER: [PatrolRunState.PERSIST_STATE],
        PatrolRunState.DEGRADED_SUMMARY: [PatrolRunState.PERSIST_STATE],
        PatrolRunState.NEEDS_HUMAN: [PatrolRunState.REQUEST_APPROVAL, PatrolRunState.PERSIST_STATE],
        PatrolRunState.CLASSIFY_FINDINGS: [PatrolRunState.RECOMMEND],
        PatrolRunState.RECOMMEND: [PatrolRunState.REQUEST_APPROVAL, PatrolRunState.PERSIST_STATE],
        PatrolRunState.REQUEST_APPROVAL: [PatrolRunState.PERSIST_STATE],
        PatrolRunState.PERSIST_STATE: [PatrolRunState.SCHEDULE_NEXT],
        PatrolRunState.SCHEDULE_NEXT: [PatrolRunState.IDLE],
    }

    def __init__(self, patrol_run_id: str) -> None:
        self.patrol_run_id = patrol_run_id
        self.current_state = PatrolRunState.IDLE
        self.status = "idle"
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None
        self.transition_history: List[Dict[str, Any]] = []

    def start(self) -> None:
        """Start the patrol round from idle into context collection."""
        if self.current_state != PatrolRunState.IDLE or self.status == "running":
            raise ValueError(f"Patrol run cannot start from {self.current_state.value}/{self.status}")
        self.started_at = current_timestamp()
        self.status = "running"
        self.current_state = PatrolRunState.COLLECT_CONTEXT
        self.transition_history.append(
            {"event": "start", "to": self.current_state.value, "at": self.started_at}
        )

    def transition_to(self, state: PatrolRunState | str) -> PatrolTransition:
        """Transition to the next state if allowed."""
        target = PatrolRunState(state)
        allowed_next = self.transitions.get(self.current_state, [])
        if target not in allowed_next:
            return PatrolTransition(
                from_state=self.current_state,
                to_state=target,
                allowed=False,
                reason=f"{target.value} is not allowed after {self.current_state.value}",
            )
        previous = self.current_state
        self.current_state = target
        if target == PatrolRunState.IDLE and previous == PatrolRunState.SCHEDULE_NEXT:
            self.status = "completed"
            self.completed_at = current_timestamp()
        self.transition_history.append(
            {
                "event": "transition",
                "from": previous.value,
                "to": target.value,
                "at": current_timestamp(),
            }
        )
        return PatrolTransition(previous, target, True, "allowed")

    def fail(self, reason: str) -> None:
        """Mark the patrol round as failed."""
        self.status = "failed"
        self.completed_at = current_timestamp()
        self.transition_history.append(
            {"event": "failed", "state": self.current_state.value, "reason": reason, "at": self.completed_at}
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize state machine state."""
        return {
            "patrol_run_id": self.patrol_run_id,
            "current_state": self.current_state.value,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "transition_history": self.transition_history,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PatrolRunStateMachine":
        """Restore a state machine from serialized state."""
        machine = cls(data["patrol_run_id"])
        machine.current_state = PatrolRunState(data.get("current_state", PatrolRunState.IDLE.value))
        machine.status = data.get("status", "idle")
        machine.started_at = data.get("started_at")
        machine.completed_at = data.get("completed_at")
        machine.transition_history = data.get("transition_history", [])
        return machine
