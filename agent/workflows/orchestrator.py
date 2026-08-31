"""Deterministic workflow state and evidence registry."""

from typing import Dict, Any, Optional, List
from pathlib import Path

from agent.workflows.state_machine import (
    WorkflowDefinition,
    WorkflowStateMachine,
    WorkflowState,
)


class WorkflowOrchestrator:
    """Track stage state without dispatching agents or platform actions.

    Model/tool execution belongs to ``GalateaSDKRuntime`` and SDK MCP tools.
    This class only records externally produced stage evidence and validates
    deterministic transitions.
    """

    def __init__(
        self,
        project_root: Path,
        mlflow_tracking_uri: str = "http://127.0.0.1:5000",
    ):
        """
        Initialize workflow orchestrator.

        Args:
            project_root: Root directory of Galatea platform
            mlflow_tracking_uri: MLflow tracking server URI
        """
        self.project_root = project_root
        self.mlflow_uri = mlflow_tracking_uri

        self._active_workflows: Dict[str, WorkflowStateMachine] = {}

    async def create_workflow(
        self,
        workflow_id: str,
        definition: WorkflowDefinition,
    ) -> str:
        """
        Create new workflow instance.

        Args:
            workflow_id: Unique workflow identifier
            definition: Workflow definition

        Returns:
            Workflow ID

        """
        if workflow_id in self._active_workflows:
            raise ValueError(f"Workflow already exists: {workflow_id}")
        self._active_workflows[workflow_id] = WorkflowStateMachine(workflow_id, definition)
        return workflow_id

    async def start_workflow(
        self,
        workflow_id: str,
    ) -> Dict[str, Any]:
        """Start state tracking and return the current workflow evidence."""
        machine = self._get_workflow(workflow_id)
        machine.start()
        return machine.get_status()

    async def record_stage_result(
        self,
        workflow_id: str,
        stage: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Record MCP-produced evidence and advance one deterministic edge."""
        machine = self._get_workflow(workflow_id)
        if machine.state != WorkflowState.RUNNING:
            raise ValueError(f"Workflow is not running: {machine.state.value}")
        if stage != machine.current_stage:
            raise ValueError(
                f"Result stage {stage!r} does not match current stage "
                f"{machine.current_stage!r}."
            )

        if result.get("status") == "failed":
            machine.fail_stage(stage, str(result.get("error") or "stage failed"))
            return machine.get_status()

        machine.complete_stage(stage, result)
        next_stages = machine.definition.get_next_stages(stage)
        if next_stages:
            transition = machine.transition_to(next_stages[0])
            if not transition.allowed:
                machine.fail_stage(stage, transition.reason)
        return machine.get_status()

    async def pause_workflow(self, workflow_id: str) -> None:
        """
        Pause workflow execution.

        Args:
            workflow_id: Workflow to pause

        """
        self._get_workflow(workflow_id).pause()

    async def resume_workflow(self, workflow_id: str) -> None:
        """
        Resume paused workflow.

        Args:
            workflow_id: Workflow to resume

        """
        self._get_workflow(workflow_id).resume()

    async def cancel_workflow(self, workflow_id: str) -> None:
        """
        Cancel workflow execution.

        Args:
            workflow_id: Workflow to cancel

        """
        self._get_workflow(workflow_id).cancel()

    async def get_workflow_status(
        self,
        workflow_id: str,
    ) -> Dict[str, Any]:
        """
        Get workflow status.

        Args:
            workflow_id: Workflow ID

        Returns:
            Status dictionary

        """
        return self._get_workflow(workflow_id).get_status()

    async def list_workflows(
        self,
        state: Optional[WorkflowState] = None,
    ) -> List[str]:
        """
        List workflow IDs.

        Args:
            state: Optional state filter

        Returns:
            List of workflow IDs

        """
        if state is None:
            return sorted(self._active_workflows)
        return sorted(
            workflow_id
            for workflow_id, machine in self._active_workflows.items()
            if machine.state == state
        )

    def _get_workflow(self, workflow_id: str) -> WorkflowStateMachine:
        try:
            return self._active_workflows[workflow_id]
        except KeyError as exc:
            raise KeyError(f"Workflow not found: {workflow_id}") from exc


# Predefined workflow definitions
DATA_TRAINING_INFERENCE_WORKFLOW = WorkflowDefinition(
    name="data-training-inference",
    stages=["data", "training", "inference"],
    transitions={
        "data": ["training"],
        "training": ["inference"],
        "inference": [],
    },
    initial_stage="data",
)

DATA_TRAINING_WORKFLOW = WorkflowDefinition(
    name="data-training",
    stages=["data", "training"],
    transitions={
        "data": ["training"],
        "training": [],
    },
    initial_stage="data",
)

TRAINING_INFERENCE_WORKFLOW = WorkflowDefinition(
    name="training-inference",
    stages=["training", "inference"],
    transitions={
        "training": ["inference"],
        "inference": [],
    },
    initial_stage="training",
)
