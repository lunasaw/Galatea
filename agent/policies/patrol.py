"""Patrol lifecycle, recommendation, and action governance policies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Sequence, Set

from agent.schemas.patrol import (
    ActionLevel,
    EvidenceRecord,
    FailureType,
    Finding,
    FindingStatus,
    Recommendation,
    RecommendationStatus,
    RiskLevel,
    Severity,
    action_level_rank,
    current_timestamp,
    stable_unique,
)


@dataclass
class ActionDecision:
    """Result of action-level patrol policy evaluation."""

    allowed: bool
    reason: str
    failure_type: Optional[str] = None
    recommended_next_action: Optional[str] = None


class PatrolLifecyclePolicy:
    """Finding/recommendation de-duplication, cooldown, and escalation."""

    def __init__(
        self,
        *,
        default_cooldown_seconds: int = 3600,
        escalate_after: int = 3,
    ) -> None:
        self.default_cooldown_seconds = default_cooldown_seconds
        self.escalate_after = escalate_after
        self.suppressed_recommendation_count = 0

    def upsert_finding(
        self,
        open_findings: List[Finding],
        finding: Finding,
        evidence: Sequence[EvidenceRecord],
        *,
        now: Optional[datetime] = None,
    ) -> Finding:
        """Insert or update a finding by fingerprint."""
        timestamp = _timestamp(now)
        evidence_ids = [record.evidence_id for record in evidence]
        for existing in open_findings:
            if existing.fingerprint == finding.fingerprint:
                existing.status = FindingStatus.OPEN
                existing.resolved_at = None
                existing.last_seen_at = timestamp
                existing.occurrence_count += 1
                existing.evidence_ids = stable_unique([*existing.evidence_ids, *finding.evidence_ids, *evidence_ids])
                existing.summary = finding.summary
                if existing.occurrence_count >= self.escalate_after:
                    existing.severity = Severity.CRITICAL
                elif _severity_rank(finding.severity) > _severity_rank(existing.severity):
                    existing.severity = finding.severity
                return existing
        finding.last_seen_at = timestamp
        finding.first_seen_at = finding.first_seen_at or timestamp
        finding.evidence_ids = stable_unique([*finding.evidence_ids, *evidence_ids])
        open_findings.append(finding)
        return finding

    def mark_resolved_if_missing(
        self,
        open_findings: List[Finding],
        *,
        active_fingerprints: Set[str],
        now: Optional[datetime] = None,
    ) -> List[Finding]:
        """Resolve open findings whose fingerprints are absent in this patrol round."""
        timestamp = _timestamp(now)
        resolved: List[Finding] = []
        for finding in open_findings:
            if finding.status == FindingStatus.OPEN and finding.fingerprint not in active_fingerprints:
                finding.status = FindingStatus.RESOLVED
                finding.resolved_at = timestamp
                finding.last_seen_at = timestamp
                resolved.append(finding)
        return resolved

    def upsert_recommendation(
        self,
        recommendations: List[Recommendation],
        recommendation: Recommendation,
        *,
        now: Optional[datetime] = None,
    ) -> tuple[Recommendation, bool]:
        """Insert recommendation or suppress duplicate pushes during cooldown."""
        current = now or datetime.now(timezone.utc)
        for existing in recommendations:
            if existing.fingerprint == recommendation.fingerprint:
                if existing.cooldown_until and _parse_timestamp(existing.cooldown_until) > current:
                    self.suppressed_recommendation_count += 1
                    return existing, False
                existing.updated_at = _timestamp(current)
                existing.status = RecommendationStatus.PUSHED
                existing.cooldown_until = _timestamp(current + timedelta(seconds=self.default_cooldown_seconds))
                existing.evidence_ids = stable_unique([*existing.evidence_ids, *recommendation.evidence_ids])
                existing.finding_ids = stable_unique([*existing.finding_ids, *recommendation.finding_ids])
                return existing, True
        recommendation.status = RecommendationStatus.PUSHED
        recommendation.cooldown_until = _timestamp(current + timedelta(seconds=self.default_cooldown_seconds))
        recommendations.append(recommendation)
        return recommendation, True


class PatrolActionPolicy:
    """Action-level patrol permission policy beyond raw tool allowlists."""

    def __init__(
        self,
        *,
        max_action_level: ActionLevel = ActionLevel.RECOMMEND,
        project_scope: Optional[List[str]] = None,
        allow_request_approval: bool = False,
        approved_approval_ids: Optional[Iterable[str]] = None,
    ) -> None:
        self.max_action_level = ActionLevel(max_action_level)
        self.project_scope = set(project_scope or [])
        self.allow_request_approval = allow_request_approval
        self.approved_approval_ids = set(approved_approval_ids or [])

    def check_action(
        self,
        *,
        action_type: str,
        action_level: ActionLevel | str,
        project_name: str,
        risk: RiskLevel | str = RiskLevel.LOW,
        evidence_ids: Optional[List[str]] = None,
        approval_request_id: Optional[str] = None,
    ) -> ActionDecision:
        """Evaluate whether a patrol action is allowed."""
        level = ActionLevel(action_level)
        normalized_risk = RiskLevel(risk)
        evidence_ids = evidence_ids or []

        if self.project_scope and project_name not in self.project_scope:
            return ActionDecision(
                allowed=False,
                reason=f"project {project_name} is outside patrol scope",
                failure_type=FailureType.PERMISSION.value,
                recommended_next_action="needs_human",
            )

        if _requires_evidence(action_type, normalized_risk) and not evidence_ids:
            return ActionDecision(
                allowed=False,
                reason=f"{action_type} requires evidence before recommendation or approval",
                failure_type=FailureType.EVIDENCE_MISSING.value,
                recommended_next_action="degraded_summary",
            )

        if level == ActionLevel.APPLY and not approval_request_id:
            return ActionDecision(
                allowed=False,
                reason=f"{action_type} requires approval before L3 apply",
                failure_type=FailureType.POLICY_BLOCKED.value,
                recommended_next_action="request_approval",
            )

        if action_level_rank(level) > action_level_rank(self.max_action_level):
            return ActionDecision(
                allowed=False,
                reason=f"{level.value} exceeds configured patrol action level {self.max_action_level.value}",
                failure_type=FailureType.POLICY_BLOCKED.value,
                recommended_next_action="request_approval" if level == ActionLevel.APPLY else "needs_human",
            )

        if level == ActionLevel.REQUEST_APPROVAL and not self.allow_request_approval:
            return ActionDecision(
                allowed=False,
                reason="approval request creation is disabled for this patrol policy",
                failure_type=FailureType.PERMISSION.value,
                recommended_next_action="needs_human",
            )

        if level == ActionLevel.APPLY:
            if approval_request_id not in self.approved_approval_ids:
                return ActionDecision(
                    allowed=False,
                    reason=f"approval {approval_request_id} is not accepted for {action_type}",
                    failure_type=FailureType.POLICY_BLOCKED.value,
                    recommended_next_action="needs_human",
                )
            if normalized_risk == RiskLevel.HIGH and not evidence_ids:
                return ActionDecision(
                    allowed=False,
                    reason=f"high-risk {action_type} requires evidence completeness",
                    failure_type=FailureType.EVIDENCE_MISSING.value,
                    recommended_next_action="degraded_summary",
                )

        return ActionDecision(allowed=True, reason="allowed")


def _severity_rank(severity: Severity | str) -> int:
    order = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}
    return order[Severity(severity)]


def _timestamp(value: Optional[datetime]) -> str:
    if value is None:
        return current_timestamp()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _requires_evidence(action_type: str, risk: RiskLevel) -> bool:
    high_risk_keywords = ["promotion", "registry", "alias", "delete", "overwrite", "training", "gpu"]
    return risk != RiskLevel.LOW or any(keyword in action_type for keyword in high_risk_keywords)
