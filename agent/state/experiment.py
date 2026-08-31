"""Experiment state tracking for training workflows."""

from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from agent.state.persistence import load_from_file, save_to_file


class ExperimentStage(str, Enum):
    """Experiment workflow stages."""

    DATA = "data"
    TRAINING = "training"
    INFERENCE = "inference"
    PROMOTION = "promotion"


class ExperimentState:
    """
    Tracks state across experiment workflow stages.

    Maintains artifact references, stage results, and transition history.
    """

    def __init__(
        self,
        experiment_id: str,
        project_name: str,
        mlflow_experiment_name: str,
    ):
        """
        Initialize experiment state.

        Args:
            experiment_id: Unique experiment identifier
            project_name: Training project name
            mlflow_experiment_name: MLflow experiment name
        """
        self.experiment_id = experiment_id
        self.project_name = project_name
        self.mlflow_experiment_name = mlflow_experiment_name

        self.current_stage: Optional[ExperimentStage] = None
        self.stage_results: Dict[str, Dict[str, Any]] = {}
        self.artifacts: Dict[str, str] = {}
        self.transitions: List[Dict[str, Any]] = []

        self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.created_at

    def set_stage(self, stage: ExperimentStage) -> None:
        """
        Transition to new stage.

        Args:
            stage: Target stage

        """
        if not isinstance(stage, ExperimentStage):
            stage = ExperimentStage(stage)
        previous = self.current_stage.value if self.current_stage else None
        self.current_stage = stage
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.transitions.append(
            {
                "from": previous,
                "to": stage.value,
                "at": self.updated_at,
            }
        )

    def record_stage_result(
        self,
        stage: ExperimentStage,
        result: Dict[str, Any],
    ) -> None:
        """
        Record result from completed stage.

        Args:
            stage: Stage that completed
            result: Stage result dictionary

        """
        if not isinstance(stage, ExperimentStage):
            stage = ExperimentStage(stage)
        self.stage_results[stage.value] = result
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def add_artifact(
        self,
        name: str,
        uri: str,
        stage: ExperimentStage,
    ) -> None:
        """
        Register artifact produced by stage.

        Args:
            name: Artifact name (e.g., "data_manifest", "checkpoint")
            uri: Artifact URI (mlflow-artifacts://, s3://, etc.)
            stage: Stage that produced artifact

        """
        if not isinstance(stage, ExperimentStage):
            stage = ExperimentStage(stage)
        self.artifacts[name] = uri
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.transitions.append(
            {
                "event": "artifact_added",
                "stage": stage.value,
                "name": name,
                "uri": uri,
                "at": self.updated_at,
            }
        )

    def get_artifact(self, name: str) -> Optional[str]:
        """
        Get artifact URI by name.

        Args:
            name: Artifact name

        Returns:
            Artifact URI or None

        """
        return self.artifacts.get(name)

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize state to dictionary.

        Returns:
            State dictionary

        """
        return {
            "experiment_id": self.experiment_id,
            "project_name": self.project_name,
            "mlflow_experiment_name": self.mlflow_experiment_name,
            "current_stage": self.current_stage.value if self.current_stage else None,
            "stage_results": self.stage_results,
            "artifacts": self.artifacts,
            "transitions": self.transitions,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExperimentState":
        """
        Deserialize state from dictionary.

        Args:
            data: State dictionary

        Returns:
            ExperimentState instance

        """
        state = cls(
            experiment_id=data["experiment_id"],
            project_name=data["project_name"],
            mlflow_experiment_name=data["mlflow_experiment_name"],
        )
        current_stage = data.get("current_stage")
        state.current_stage = ExperimentStage(current_stage) if current_stage else None
        state.stage_results = data.get("stage_results", {})
        state.artifacts = data.get("artifacts", {})
        state.transitions = data.get("transitions", [])
        state.created_at = data.get("created_at", state.created_at)
        state.updated_at = data.get("updated_at", state.updated_at)
        return state


class ExperimentStateManager:
    """
    Manages experiment state persistence and retrieval.
    """

    def __init__(self, storage_path: Optional[str] = None):
        """
        Initialize experiment state manager.

        Args:
            storage_path: Optional path for persistent storage
        """
        self.storage_path = storage_path
        self._states: Dict[str, ExperimentState] = {}

    async def save_state(self, state: ExperimentState) -> None:
        """
        Save experiment state.

        Args:
            state: ExperimentState to save

        """
        self._states[state.experiment_id] = state
        if self.storage_path:
            path = self._state_path(state.experiment_id)
            await save_to_file(state.to_dict(), path)

    async def load_state(self, experiment_id: str) -> Optional[ExperimentState]:
        """
        Load experiment state by ID.

        Args:
            experiment_id: Experiment identifier

        Returns:
            ExperimentState or None

        """
        if experiment_id in self._states:
            return self._states[experiment_id]
        if self.storage_path:
            path = self._state_path(experiment_id)
            if path.exists():
                state = ExperimentState.from_dict(await load_from_file(path))
                self._states[experiment_id] = state
                return state
        return None

    async def list_experiments(
        self,
        project_name: Optional[str] = None,
    ) -> List[str]:
        """
        List experiment IDs.

        Args:
            project_name: Optional project name filter

        Returns:
            List of experiment IDs

        """
        ids = set(self._states)
        if self.storage_path:
            root = Path(self.storage_path)
            if root.exists():
                ids.update(path.stem for path in root.glob("*.json"))

        if project_name is None:
            return sorted(ids)

        matching = []
        for experiment_id in sorted(ids):
            state = await self.load_state(experiment_id)
            if state and state.project_name == project_name:
                matching.append(experiment_id)
        return matching

    def _state_path(self, experiment_id: str) -> Path:
        if not self.storage_path:
            raise ValueError("storage_path is not configured")
        return Path(self.storage_path) / f"{experiment_id}.json"
