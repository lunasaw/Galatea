"""
Workflow orchestration for multi-stage agent coordination.

Provides workflow state machines, orchestration, and predefined workflows.

Key components:
- WorkflowStateMachine: State management and transitions
- WorkflowOrchestrator: Multi-agent coordination
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
from agent.workflows.patrol import PatrolRunState, PatrolRunStateMachine, PatrolTransition

__all__ = [
    # State machine
    "WorkflowState",
    "StageState",
    "StageTransition",
    "WorkflowDefinition",
    "WorkflowStateMachine",
    "PatrolRunState",
    "PatrolRunStateMachine",
    "PatrolTransition",
    # Orchestrator
    "WorkflowOrchestrator",
    # Predefined workflows
    "DATA_TRAINING_INFERENCE_WORKFLOW",
    "DATA_TRAINING_WORKFLOW",
    "TRAINING_INFERENCE_WORKFLOW",
]
