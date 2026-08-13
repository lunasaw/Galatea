"""Claude SDK integration helpers for patrol-push agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.core import AgentSDKConfig
from agent.policies.patrol import PatrolActionPolicy
from agent.schemas.patrol import (
    ActionLevel,
    FailureType,
    PatrolFailure,
    PatrolMemory,
    PatrolRunResult,
    PatrolRunStatus,
    Recommendation,
    RecommendationType,
    Recoverability,
    new_id,
)

PATROL_ALLOWED_TOOLS = [
    "mcp__galatea-platform__list_training_projects",
    "mcp__galatea-platform__inspect_project_structure",
    "mcp__galatea-platform__check_service_health",
    "mcp__galatea-platform__inspect_mlflow_experiment",
    "mcp__galatea-platform__inspect_ray_status",
]


PATROL_SYSTEM_PROMPT = """You are the Galatea patrol-push agent.

Operate as a read-mostly inspection and recommendation agent:
- Preserve evidence IDs, raw_ref URIs, Run IDs, Ray IDs, artifact URIs, and approval IDs.
- Do not execute long training, registry alias changes, deletion, overwrite, or production traffic changes.
- Generate structured findings, evidence-backed recommendations, and approval requests only.
- Keep raw logs out of model context; cite evidence IDs and raw_ref URIs instead.
"""


def patrol_run_result_json_schema() -> Dict[str, Any]:
    """Return the Pydantic JSON schema used for structured SDK output."""
    return PatrolRunResult.model_json_schema()


def build_patrol_context_prompt(memory: Optional[PatrolMemory]) -> str:
    """Build a compact prompt view from authoritative patrol memory."""
    if memory is None:
        return "No prior patrol memory is available. Start with read-only inspection."
    lines = [
        "Authoritative patrol memory view:",
        f"- patrol_run_id: {memory.patrol_run_id}",
        f"- project_name: {memory.project_name}",
        f"- next_check_at: {memory.next_check_at or 'not scheduled'}",
        f"- summary: {memory.summary}",
        "- open_findings:",
    ]
    if memory.open_findings:
        for finding in memory.open_findings:
            lines.append(
                f"  - {finding.finding_id} {finding.type} {finding.severity.value}: "
                f"{finding.summary} evidence={','.join(finding.evidence_ids)}"
            )
    else:
        lines.append("  - none")
    lines.append("- evidence_index:")
    if memory.evidence_index:
        for evidence in memory.evidence_index:
            lines.append(
                f"  - {evidence.evidence_id} {evidence.kind} {evidence.source_uri} "
                f"raw={evidence.raw_ref.uri} digest={evidence.raw_ref.digest}: {evidence.summary}"
            )
    else:
        lines.append("  - none")
    return "\n".join(lines)


def make_patrol_sdk_config(
    *,
    project_root: Path | str,
    project_scope: Optional[List[str]] = None,
    memory: Optional[PatrolMemory] = None,
    allow_request_approval: bool = False,
    max_turns: int = 8,
    max_budget_usd: float = 0.20,
) -> AgentSDKConfig:
    """Create a safe SDK runtime config for patrol-push LLM summarization."""
    prompt = f"{PATROL_SYSTEM_PROMPT}\n\n{build_patrol_context_prompt(memory)}"
    allowed_tools = list(PATROL_ALLOWED_TOOLS)
    if allow_request_approval:
        # L2 is still represented as structured output, not an apply tool.
        allowed_tools.append("mcp__galatea-platform__request_approval")
    return AgentSDKConfig(
        project_root=Path(project_root),
        agent_type="patrol-push",
        project_name=project_scope[0] if project_scope else (memory.project_name if memory else None),
        allowed_tools=allowed_tools,
        disallowed_tools=["Bash", "Write", "Edit", "MultiEdit"],
        tools=[],
        permission_mode="dontAsk",
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        output_schema=patrol_run_result_json_schema(),
        system_prompt=prompt,
    )


def validate_llm_patrol_result(
    candidate: Dict[str, Any],
    *,
    action_policy: PatrolActionPolicy,
) -> PatrolRunResult:
    """Validate LLM output as candidates, then attach policy failures if needed."""
    result = PatrolRunResult.model_validate(candidate)
    result.validate_traceability()
    failures: List[PatrolFailure] = list(result.failures)
    for recommendation in result.recommendations:
        decision = action_policy.check_action(
            action_type=recommendation.type.value,
            action_level=_recommendation_action_level(recommendation),
            project_name=str(recommendation.target.get("project_name") or (result.project_scope[0] if result.project_scope else "platform")),
            risk=recommendation.risk,
            evidence_ids=recommendation.evidence_ids,
            approval_request_id=recommendation.approval_request_id,
        )
        if not decision.allowed:
            recommendation.metadata["policy_decision"] = decision.reason
            failures.append(
                PatrolFailure(
                    failure_id=new_id("fl"),
                    failure_type=FailureType(decision.failure_type or FailureType.POLICY_BLOCKED.value),
                    recoverability=Recoverability.NEEDS_INPUT,
                    recommended_next_action=decision.recommended_next_action or "needs_human",
                    message=decision.reason,
                )
            )
    result.failures = failures
    if failures and any(f.failure_type == FailureType.POLICY_BLOCKED for f in failures):
        result.status = PatrolRunStatus.NEEDS_APPROVAL
    elif failures and result.status == PatrolRunStatus.OK:
        result.status = PatrolRunStatus.WARNING
    return result


def _recommendation_action_level(recommendation: Recommendation) -> ActionLevel:
    if recommendation.type in {
        RecommendationType.REQUEST_TRAINING_APPROVAL,
        RecommendationType.REQUEST_PROMOTION_REVIEW,
    }:
        return ActionLevel.REQUEST_APPROVAL
    if recommendation.type == RecommendationType.RERUN_SMOKE and not recommendation.requires_approval:
        return ActionLevel.APPLY
    return ActionLevel.RECOMMEND
