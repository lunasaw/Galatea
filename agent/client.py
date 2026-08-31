"""High-level Galatea agent client."""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Dict, Literal, Optional

from agent.core import AgentSDKConfig, GalateaSDKRuntime, SDKRunResult
from agent.runtime import default_platform_allowed_tools


class GalateaAgentClient:
    """Convenience client for platform-aware agent operations."""

    def __init__(
        self,
        project_root: Path,
        mlflow_tracking_uri: str = "http://127.0.0.1:5000",
        ray_address: Optional[str] = None,
        minio_endpoint: str = "http://127.0.0.1:9000",
        model: str = "claude-opus-5",
        max_budget_usd: float = 0.20,
        skills: list[str] | Literal["all"] | None = "all",
    ) -> None:
        self.project_root = project_root
        self.mlflow_uri = mlflow_tracking_uri
        self.ray_address = ray_address
        self.minio_endpoint = minio_endpoint
        self.model = model
        self.max_budget_usd = max_budget_usd
        self.skills = skills
        self._runtime: GalateaSDKRuntime | None = None

    async def __aenter__(self) -> "GalateaAgentClient":
        self._runtime = GalateaSDKRuntime(
            AgentSDKConfig(
                project_root=self.project_root,
                model=self.model,
                agent_type="client",
                allowed_tools=default_platform_allowed_tools(),
                max_budget_usd=self.max_budget_usd,
                skills=self.skills,
            )
        )
        await self._runtime.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._runtime is not None:
            await self._runtime.__aexit__(*args)
            self._runtime = None

    async def query(self, prompt: str) -> AsyncIterator[Any]:
        """Stream raw SDK messages for a prompt."""
        runtime = self._require_runtime()
        async for message in runtime.stream_query(prompt):
            yield message

    async def run(self, prompt: str) -> SDKRunResult:
        """Run a prompt and return a validated SDK result."""
        return await self._require_runtime().query(prompt)

    async def train_model(
        self,
        project_name: str,
        config_path: Path,
        experiment_name: str,
    ) -> Dict[str, Any]:
        """
        Produce a safe training plan using the current read-only foundation.

        Long training submission is intentionally not performed here; later
        stage-specific tools can turn this plan into approved Ray jobs.
        """
        prompt = f"""Create a safe Galatea training orchestration plan.

Project: {project_name}
Config path: {config_path}
MLflow experiment: {experiment_name}
Tracking URI: {self.mlflow_uri}
Ray address: {self.ray_address or 'auto/local'}

Use only read-only Galatea inspection tools. Do not submit training, do not use
test metrics for search, do not promote models, and do not call Bash."""
        result = await self.run(prompt)
        return {
            "status": "planned",
            "project_name": project_name,
            "config_path": str(config_path),
            "experiment_name": experiment_name,
            "response": result.text,
            "tool_calls": [call.name for call in result.tool_calls],
            "cost_usd": result.total_cost_usd,
        }

    async def optimize_experiment(
        self,
        experiment_name: str,
        objective_metric: str,
        objective_mode: str = "max",
    ) -> Dict[str, Any]:
        """Analyze an MLflow experiment and return safe optimization advice."""
        if objective_mode not in {"max", "min"}:
            raise ValueError("objective_mode must be 'max' or 'min'")
        prompt = f"""Analyze MLflow experiment '{experiment_name}' for optimization.

Tracking URI: {self.mlflow_uri}
Objective metric: {objective_metric}
Objective mode: {objective_mode}

Use read-only MLflow/project inspection tools. Compare only compatible runs and
state when evidence is insufficient. Do not submit jobs or change Registry aliases."""
        result = await self.run(prompt)
        return {
            "status": "success",
            "experiment_name": experiment_name,
            "objective_metric": objective_metric,
            "objective_mode": objective_mode,
            "response": result.text,
            "tool_calls": [call.name for call in result.tool_calls],
            "cost_usd": result.total_cost_usd,
        }

    def _require_runtime(self) -> GalateaSDKRuntime:
        if self._runtime is None:
            raise RuntimeError("GalateaAgentClient is not connected. Use 'async with'.")
        return self._runtime
