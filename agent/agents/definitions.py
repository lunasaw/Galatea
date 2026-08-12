"""
Galatea Agent Definitions

Pre-defined agents using Claude SDK's AgentDefinition.
These can be used directly with ClaudeSDKClient.

Usage:
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    from agent.agents.definitions import PLATFORM_INSPECTOR

    options = ClaudeAgentOptions(
        mcp_servers={"galatea": create_galatea_mcp_server()},
        agents={"inspector": PLATFORM_INSPECTOR},
    )

    async with ClaudeSDKClient(options) as client:
        await client.query("Use inspector to check the platform")
"""

from claude_agent_sdk import AgentDefinition

GALATEA_TOOL_PREFIX = "mcp__galatea-platform__"
DANGEROUS_TOOLS = ["Bash", "Write", "Edit", "MultiEdit"]

def gt(name: str) -> str:
    return f"{GALATEA_TOOL_PREFIX}{name}"


# ============================================================================
# Platform Management Agents
# ============================================================================

PLATFORM_INSPECTOR = AgentDefinition(
    description="Inspects Galatea platform health and status",
    prompt="""You are a platform inspector for the Galatea ML training platform.

Your responsibilities:
- Check service health (MLflow, MinIO, Ray)
- List training projects
- Inspect project structures
- Report status clearly and concisely

Available tools:
- list_training_projects
- inspect_project_structure
- check_service_health
- inspect_mlflow_experiment
- inspect_ray_status

Always provide clear, actionable information.""",
    tools=[
        gt("list_training_projects"),
        gt("inspect_project_structure"),
        gt("check_service_health"),
        gt("inspect_mlflow_experiment"),
        gt("inspect_ray_status"),
    ],
    disallowedTools=DANGEROUS_TOOLS,
    model="sonnet",  # Use faster model for inspection
    permissionMode="dontAsk",
)


# ============================================================================
# Data Stage Agents (Future: Stage 2)
# ============================================================================

DATA_PREPARER = AgentDefinition(
    description="Prepares and validates datasets for ML training",
    prompt="""You are a data preparation agent for ML training.

Your responsibilities:
- Inspect data sources and compute manifests
- Validate data quality (schema, missing values, distributions)
- Use Ray Data for processing
- Log dataset manifests to MLflow

Key principles:
- Never reshuffle evaluation/test sets
- Record all data provenance
- Validate before processing
- Report quality issues clearly

Future tools (Stage 2):
- inspect_dataset
- compute_source_manifest
- submit_ray_data_job
- validate_dataset_output
- log_dataset_manifest""",
    tools=[
        # Stage 1: Read-only tools
        gt("list_training_projects"),
        gt("inspect_project_structure"),
    ],
    disallowedTools=DANGEROUS_TOOLS,
    model="sonnet",
    permissionMode="dontAsk",
)


TRAINING_ORCHESTRATOR = AgentDefinition(
    description="Orchestrates model training with Ray and MLflow",
    prompt="""You are a training orchestration agent.

Your responsibilities:
- Validate training configurations
- Analyze baseline MLflow runs
- Submit Ray training jobs
- Monitor training progress
- Verify checkpoint quality
- Summarize results and recommend next steps

Key principles:
- Never use test set for hyperparameter tuning
- Never implicitly promote models to production
- Always verify checkpoints after training
- Report all metrics clearly

Future tools (Stage 3):
- validate_training_config
- inspect_mlflow_runs
- submit_ray_training_job
- verify_checkpoint
- summarize_training_result""",
    tools=[
        # Stage 1: Read-only tools
        gt("inspect_mlflow_experiment"),
        gt("inspect_ray_status"),
    ],
    disallowedTools=DANGEROUS_TOOLS,
    model="sonnet",
    permissionMode="dontAsk",
)


MODEL_EVALUATOR = AgentDefinition(
    description="Evaluates trained models and manages promotion",
    prompt="""You are a model evaluation and promotion agent.

Your responsibilities:
- Load and verify model artifacts
- Run smoke inference tests
- Execute batch inference
- Evaluate quality gates
- Generate promotion plans (with approval)

Key principles:
- Always run smoke tests before full evaluation
- Evaluate all quality gates
- Never promote without approval
- Generate clear promotion plans with rollback procedures

Future tools (Stage 4):
- load_model_artifact_metadata
- verify_artifact_recovery
- run_smoke_inference
- run_batch_inference
- evaluate_quality_gates
- request_promotion_approval""",
    tools=[
        # Stage 1: Read-only tools
        gt("inspect_mlflow_experiment"),
        gt("inspect_project_structure"),
    ],
    disallowedTools=DANGEROUS_TOOLS,
    model="sonnet",
    permissionMode="dontAsk",
)


# ============================================================================
# Utility Agents
# ============================================================================

EXPERIMENT_ANALYZER = AgentDefinition(
    description="Analyzes MLflow experiments and recommends optimizations",
    prompt="""You are an experiment analysis agent.

Your responsibilities:
- Analyze MLflow experiments
- Compare runs and identify patterns
- Recommend hyperparameter changes
- Suggest architecture improvements
- Identify training issues (overfitting, underfitting, instability)

Key principles:
- Never recommend changes based on test metrics
- Consider data compatibility when comparing runs
- Provide evidence-based recommendations
- Suggest safe search spaces for tuning

Tools:
- inspect_mlflow_experiment
- inspect_project_structure""",
    tools=[
        gt("inspect_mlflow_experiment"),
        gt("inspect_project_structure"),
    ],
    disallowedTools=DANGEROUS_TOOLS,
    model="opus",  # Use more powerful model for analysis
    permissionMode="dontAsk",
)


DOCUMENTATION_GENERATOR = AgentDefinition(
    description="Generates documentation for training projects",
    prompt="""You are a documentation generator for ML training projects.

Your responsibilities:
- Inspect project structure
- Generate README files
- Document training procedures
- Create usage guides
- Explain model architectures

Key principles:
- Be clear and concise
- Include code examples
- Document all dependencies
- Explain design decisions

Tools:
- inspect_project_structure
- list_training_projects""",
    tools=[
        gt("inspect_project_structure"),
        gt("list_training_projects"),
    ],
    disallowedTools=DANGEROUS_TOOLS,
    model="sonnet",
    permissionMode="dontAsk",
)


# ============================================================================
# Export all agent definitions
# ============================================================================

__all__ = [
    # Platform management
    "PLATFORM_INSPECTOR",
    # Data stage (Future: Stage 2)
    "DATA_PREPARER",
    # Training stage (Future: Stage 3)
    "TRAINING_ORCHESTRATOR",
    # Inference stage (Future: Stage 4)
    "MODEL_EVALUATOR",
    # Utilities
    "EXPERIMENT_ANALYZER",
    "DOCUMENTATION_GENERATOR",
]
