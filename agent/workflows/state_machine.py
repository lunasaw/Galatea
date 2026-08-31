"""Workflow state machine for multi-stage orchestration."""

from typing import Dict, Any, Optional, List
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone


class WorkflowState(str, Enum):
    """Workflow execution states."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class StageState(str, Enum):
    """Individual stage states."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class StageTransition:
    """
    Stage transition with validation.

    Represents transition from one stage to another with prerequisites.
    """

    from_stage: str
    to_stage: str
    allowed: bool
    reason: str
    prerequisites: List[str] = field(default_factory=list)

    def validate(self) -> bool:
        """
        Validate if transition is allowed.

        Returns:
            True if transition is valid

        """
        return self.allowed


@dataclass
class WorkflowDefinition:
    """
    Workflow definition with stages and transitions.
    """

    name: str
    stages: List[str]
    transitions: Dict[str, List[str]]  # stage -> list of allowed next stages
    initial_stage: str

    def validate_transition(
        self,
        from_stage: str,
        to_stage: str,
    ) -> StageTransition:
        """
        Validate stage transition.

        Args:
            from_stage: Current stage
            to_stage: Target stage

        Returns:
            StageTransition with validation result

        """
        if from_stage not in self.stages:
            return StageTransition(from_stage, to_stage, False, f"Unknown from_stage: {from_stage}")
        if to_stage not in self.stages:
            return StageTransition(from_stage, to_stage, False, f"Unknown to_stage: {to_stage}")
        allowed_next = self.transitions.get(from_stage, [])
        allowed = to_stage in allowed_next
        reason = "allowed" if allowed else f"{to_stage} is not allowed after {from_stage}"
        return StageTransition(from_stage, to_stage, allowed, reason)

    def get_next_stages(self, current_stage: str) -> List[str]:
        """
        Get allowed next stages.

        Args:
            current_stage: Current stage name

        Returns:
            List of allowed next stages

        """
        if current_stage not in self.stages:
            raise ValueError(f"Unknown stage: {current_stage}")
        return list(self.transitions.get(current_stage, []))


class WorkflowStateMachine:
    """
    State machine for workflow execution.

    Manages workflow state, stage transitions, and execution history.
    """

    def __init__(
        self,
        workflow_id: str,
        definition: WorkflowDefinition,
    ):
        """
        Initialize workflow state machine.

        Args:
            workflow_id: Unique workflow identifier
            definition: Workflow definition
        """
        self.workflow_id = workflow_id
        self.definition = definition

        self.state = WorkflowState.PENDING
        self.current_stage: Optional[str] = None
        self.stage_states: Dict[str, StageState] = {
            stage: StageState.PENDING for stage in definition.stages
        }
        self.stage_results: Dict[str, Dict[str, Any]] = {}
        self.transition_history: List[Dict[str, Any]] = []

        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None

    def start(self) -> None:
        """
        Start workflow execution.

        """
        if self.state != WorkflowState.PENDING:
            raise ValueError(f"Workflow cannot start from state {self.state.value}")
        if self.definition.initial_stage not in self.definition.stages:
            raise ValueError(f"Invalid initial stage: {self.definition.initial_stage}")
        self.state = WorkflowState.RUNNING
        self.current_stage = self.definition.initial_stage
        self.stage_states[self.current_stage] = StageState.RUNNING
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.transition_history.append(
            {"event": "start", "to": self.current_stage, "at": self.started_at}
        )

    def transition_to(self, stage: str) -> StageTransition:
        """
        Transition to next stage.

        Args:
            stage: Target stage name

        Returns:
            StageTransition result

        """
        if self.state != WorkflowState.RUNNING:
            transition = StageTransition(self.current_stage or "", stage, False, f"Workflow is {self.state.value}")
            return transition
        if self.current_stage is None:
            raise ValueError("Workflow has not been started")

        transition = self.definition.validate_transition(self.current_stage, stage)
        if not transition.allowed:
            return transition

        if self.stage_states[self.current_stage] == StageState.RUNNING:
            self.stage_states[self.current_stage] = StageState.COMPLETED
        self.current_stage = stage
        self.stage_states[stage] = StageState.RUNNING
        self.transition_history.append(
            {
                "event": "transition",
                "from": transition.from_stage,
                "to": transition.to_stage,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return transition

    def complete_stage(
        self,
        stage: str,
        result: Dict[str, Any],
    ) -> None:
        """
        Mark stage as completed.

        Args:
            stage: Stage name
            result: Stage result data

        """
        if stage not in self.stage_states:
            raise ValueError(f"Unknown stage: {stage}")
        self.stage_states[stage] = StageState.COMPLETED
        self.stage_results[stage] = result
        self.transition_history.append(
            {"event": "complete_stage", "stage": stage, "at": datetime.now(timezone.utc).isoformat()}
        )
        if all(state in {StageState.COMPLETED, StageState.SKIPPED} for state in self.stage_states.values()):
            self.state = WorkflowState.COMPLETED
            self.completed_at = datetime.now(timezone.utc).isoformat()

    def fail_stage(
        self,
        stage: str,
        error: str,
    ) -> None:
        """
        Mark stage as failed.

        Args:
            stage: Stage name
            error: Error message

        """
        if stage not in self.stage_states:
            raise ValueError(f"Unknown stage: {stage}")
        self.stage_states[stage] = StageState.FAILED
        self.stage_results[stage] = {"status": "failed", "error": error}
        self.state = WorkflowState.FAILED
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.transition_history.append(
            {
                "event": "fail_stage",
                "stage": stage,
                "error": error,
                "at": self.completed_at,
            }
        )

    def pause(self) -> None:
        """
        Pause workflow execution.

        """
        if self.state != WorkflowState.RUNNING:
            raise ValueError(f"Workflow cannot pause from state {self.state.value}")
        self.state = WorkflowState.PAUSED

    def resume(self) -> None:
        """
        Resume paused workflow.

        """
        if self.state != WorkflowState.PAUSED:
            raise ValueError(f"Workflow cannot resume from state {self.state.value}")
        self.state = WorkflowState.RUNNING

    def cancel(self) -> None:
        """
        Cancel workflow execution.

        """
        if self.state in {WorkflowState.COMPLETED, WorkflowState.FAILED, WorkflowState.CANCELLED}:
            raise ValueError(f"Workflow cannot cancel from state {self.state.value}")
        self.state = WorkflowState.CANCELLED
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def get_status(self) -> Dict[str, Any]:
        """
        Get workflow status.

        Returns:
            Status dictionary with state, stages, and results

        """
        return self.to_dict()

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize state machine to dictionary.

        Returns:
            State dictionary

        """
        return {
            "workflow_id": self.workflow_id,
            "definition": {
                "name": self.definition.name,
                "stages": self.definition.stages,
                "transitions": self.definition.transitions,
                "initial_stage": self.definition.initial_stage,
            },
            "state": self.state.value,
            "current_stage": self.current_stage,
            "stage_states": {stage: state.value for stage, state in self.stage_states.items()},
            "stage_results": self.stage_results,
            "transition_history": self.transition_history,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        definition: WorkflowDefinition,
    ) -> "WorkflowStateMachine":
        """
        Deserialize state machine from dictionary.

        Args:
            data: State dictionary
            definition: Workflow definition

        Returns:
            WorkflowStateMachine instance

        """
        machine = cls(workflow_id=data["workflow_id"], definition=definition)
        machine.state = WorkflowState(data.get("state", WorkflowState.PENDING.value))
        machine.current_stage = data.get("current_stage")
        machine.stage_states = {
            stage: StageState(state)
            for stage, state in data.get("stage_states", {}).items()
        } or machine.stage_states
        machine.stage_results = data.get("stage_results", {})
        machine.transition_history = data.get("transition_history", [])
        machine.started_at = data.get("started_at")
        machine.completed_at = data.get("completed_at")
        return machine
