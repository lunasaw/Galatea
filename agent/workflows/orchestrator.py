"""Workflow orchestrator for multi-agent coordination."""

from typing import Dict, Any, Optional, List
from pathlib import Path

from agent.workflows.state_machine import (
    WorkflowDefinition,
    WorkflowStateMachine,
    WorkflowState,
)


class WorkflowOrchestrator:
    """
    Orchestrates multi-agent workflows.

    Coordinates execution across multiple agents, manages state transitions,
    and aggregates results.
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

    async def execute_workflow(
        self,
        workflow_id: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Execute workflow to completion.

        Args:
            workflow_id: Workflow to execute
            context: Execution context (project_name, config, etc.)

        Returns:
            Workflow results

        The context may include ``stage_handlers`` mapping stage name to an
        async callable. Without a handler, stages complete with a skipped result.
        """
        machine = self._get_workflow(workflow_id)
        if machine.state == WorkflowState.PENDING:
            machine.start()

        while machine.state == WorkflowState.RUNNING and machine.current_stage:
            stage = machine.current_stage
            result = await self.execute_stage(workflow_id, stage, context.get(stage, context))
            if result.get("status") == "failed":
                machine.fail_stage(stage, result.get("error", "stage failed"))
                break
            machine.complete_stage(stage, result)

            next_stages = machine.definition.get_next_stages(stage)
            if not next_stages:
                if machine.state != WorkflowState.COMPLETED:
                    machine.state = WorkflowState.COMPLETED
                    machine.completed_at = machine.completed_at or result.get("completed_at")
                break
            transition = machine.transition_to(next_stages[0])
            if not transition.allowed:
                machine.fail_stage(stage, transition.reason)
                break

        return machine.get_status()

    async def execute_stage(
        self,
        workflow_id: str,
        stage: str,
        stage_input: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Execute single workflow stage.

        Args:
            workflow_id: Workflow ID
            stage: Stage name
            stage_input: Stage input data

        Returns:
            Stage result

        If no stage handler is configured, this returns a skipped stage result.
        """
        self._get_workflow(workflow_id)
        handlers = stage_input.get("stage_handlers") if isinstance(stage_input, dict) else None
        handler = handlers.get(stage) if isinstance(handlers, dict) else None
        if handler is None:
            return {
                "stage": stage,
                "status": "skipped",
                "reason": "No stage handler configured",
            }
        result = handler(stage_input)
        if hasattr(result, "__await__"):
            result = await result
        return result

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
