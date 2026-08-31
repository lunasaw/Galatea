"""
Workflow state and evidence helpers.

Provides workflow state machines, orchestration, and predefined workflows.

Key components:
- WorkflowStateMachine: State management and transitions
- WorkflowOrchestrator: deterministic state/evidence registry
- WorkflowDefinition: Workflow structure and validation
- Predefined workflows: Common training workflow patterns

Future: Stage 2+ will implement execution logic.
"""

from agent.workflows.state_machine import (
    WorkflowState,
    StageState,
    StageTransition,
    WorkflowDefinition,
    WorkflowStateMachine,
)
from agent.workflows.orchestrator import (
    WorkflowOrchestrator,
    DATA_TRAINING_INFERENCE_WORKFLOW,
    DATA_TRAINING_WORKFLOW,
    TRAINING_INFERENCE_WORKFLOW,
)

__all__ = [
    # State machine
    "WorkflowState",
    "StageState",
    "StageTransition",
    "WorkflowDefinition",
    "WorkflowStateMachine",
    # Orchestrator
    "WorkflowOrchestrator",
    # Predefined workflows
    "DATA_TRAINING_INFERENCE_WORKFLOW",
    "DATA_TRAINING_WORKFLOW",
    "TRAINING_INFERENCE_WORKFLOW",
]
