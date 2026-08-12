"""Agent definition framework for Galatea."""

from __future__ import annotations

from typing import Optional, List, Dict, Any, Literal
from dataclasses import dataclass, field

from claude_agent_sdk import AgentDefinition as SDKAgentDefinition


# Type aliases matching Claude SDK
PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"]
MemoryScope = Literal["user", "project", "local"]
EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]


@dataclass
class AgentDefinition:
    """
    Agent definition configuration.

    Defines agent behavior, tools, permissions, and execution parameters.
    Reference: Claude SDK's AgentDefinition.
    """

    name: str
    description: str
    prompt: str

    # Tool configuration
    tools: Optional[List[str]] = None
    disallowed_tools: Optional[List[str]] = None

    # Model configuration
    model: Optional[str] = None  # "sonnet", "opus", "haiku", or full model ID

    # Skills
    skills: Optional[List[str]] = None

    # Memory scope
    memory: Optional[MemoryScope] = None

    # MCP servers
    mcp_servers: Optional[List[str | Dict[str, Any]]] = None

    # Initial prompt
    initial_prompt: Optional[str] = None

    # Execution limits
    max_turns: Optional[int] = None
    max_budget_usd: Optional[float] = None

    # Execution mode
    background: bool = False
    effort: Optional[EffortLevel | int] = None
    permission_mode: Optional[PermissionMode] = None

    # Galatea-specific extensions
    project_name: Optional[str] = None
    mlflow_experiment_name: Optional[str] = None

    def validate(self) -> bool:
        """
        Validate agent definition.

        Returns:
            True if valid

        """
        if not self.name:
            raise ValueError("Agent name is required")
        if not self.description:
            raise ValueError("Agent description is required")
        if not self.prompt:
            raise ValueError("Agent prompt is required")
        if self.permission_mode == "bypassPermissions":
            raise ValueError("Galatea agents must not use bypassPermissions")
        return True

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary.

        Returns:
            Dictionary representation

        """
        self.validate()
        return {
            "name": self.name,
            "description": self.description,
            "prompt": self.prompt,
            "tools": self.tools,
            "disallowed_tools": self.disallowed_tools,
            "model": self.model,
            "skills": self.skills,
            "memory": self.memory,
            "mcp_servers": self.mcp_servers,
            "initial_prompt": self.initial_prompt,
            "max_turns": self.max_turns,
            "max_budget_usd": self.max_budget_usd,
            "background": self.background,
            "effort": self.effort,
            "permission_mode": self.permission_mode,
            "project_name": self.project_name,
            "mlflow_experiment_name": self.mlflow_experiment_name,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentDefinition":
        """
        Create from dictionary.

        Args:
            data: Dictionary data

        Returns:
            AgentDefinition instance

        """
        return cls(**data)

    def to_sdk(self) -> SDKAgentDefinition:
        """Convert to Claude SDK AgentDefinition."""
        self.validate()
        return SDKAgentDefinition(
            description=self.description,
            prompt=self.prompt,
            tools=self.tools,
            disallowedTools=self.disallowed_tools,
            model=self.model,
            skills=self.skills,
            memory=self.memory,
            mcpServers=self.mcp_servers,
            initialPrompt=self.initial_prompt,
            maxTurns=self.max_turns,
            background=self.background,
            effort=self.effort,
            permissionMode=self.permission_mode,
        )


@dataclass
class AgentMetadata:
    """
    Agent execution metadata.

    Tracks runtime information about agent execution.
    """

    agent_name: str
    session_id: str
    started_at: str

    completed_at: Optional[str] = None
    total_cost_usd: Optional[float] = None
    total_tokens: Optional[int] = None
    num_turns: Optional[int] = None
    status: Optional[str] = None

    # Result tracking
    result_summary: Optional[str] = None
    artifacts: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


# Predefined agent definitions for common patterns
INSPECTION_AGENT = AgentDefinition(
    name="inspection-agent",
    description="Read-only platform inspection agent",
    prompt="Inspect Galatea platform state and report findings.",
    tools=["list_training_projects", "inspect_project_structure", "check_service_health"],
    permission_mode="dontAsk",
)

DATA_AGENT = AgentDefinition(
    name="data-agent",
    description="Data preparation and validation agent",
    prompt="Prepare and validate datasets for training.",
    tools=[
        "inspect_dataset",
        "compute_source_manifest",
        "propose_ray_data_plan",
        "submit_ray_data_job",
        "validate_dataset_output",
    ],
    permission_mode="acceptEdits",
)

TRAINING_AGENT = AgentDefinition(
    name="training-agent",
    description="Training orchestration agent",
    prompt="Orchestrate model training with Ray and MLflow.",
    tools=[
        "validate_training_config",
        "inspect_mlflow_runs",
        "submit_ray_training_job",
        "verify_checkpoint",
    ],
    permission_mode="acceptEdits",
)

INFERENCE_AGENT = AgentDefinition(
    name="inference-agent",
    description="Inference and model evaluation agent",
    prompt="Run inference and evaluate model quality.",
    tools=[
        "load_model_artifact_metadata",
        "verify_artifact_recovery",
        "run_smoke_inference",
        "evaluate_quality_gates",
    ],
    permission_mode="acceptEdits",
)
