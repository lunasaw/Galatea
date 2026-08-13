"""Structured patrol-push agent schemas.

The patrol session is the source of truth for long-lived findings, evidence,
and recommendations. LLM context should receive compacted views of these
objects, not raw logs or mutable platform state.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Set

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator


SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
REQUIRED_PRESERVE_FIELDS = [
    "patrol_run_id",
    "project_name",
    "stage",
    "ray_job_id",
    "submission_id",
    "mlflow_run_id",
    "experiment_name",
    "artifact_uri",
    "manifest_digest",
    "model_artifact_uri",
    "registry_action",
    "approval_request_id",
    "finding_id",
    "recommendation_id",
    "next_check_at",
    "unresolved_errors",
]


def current_timestamp() -> str:
    """Return a UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    """Create a compact namespaced identifier."""
    return f"{prefix}_{uuid.uuid4().hex}"


def canonical_json(data: Any) -> str:
    """Serialize data in a deterministic form for digesting and fingerprints."""
    return json.dumps(_jsonable(data), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_digest(data: Any) -> str:
    """Return sha256 digest for arbitrary JSON-like data."""
    return "sha256:" + hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def make_fingerprint(data: Any) -> str:
    """Return a stable fingerprint for a governance object."""
    return sha256_digest(data)


def stable_unique(values: Iterable[str]) -> List[str]:
    """Preserve first-seen order while removing duplicate strings."""
    seen: Set[str] = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def action_level_rank(level: ActionLevel | str) -> int:
    """Return comparable rank for a patrol action level."""
    normalized = ActionLevel(level)
    order = {
        ActionLevel.INSPECT: 0,
        ActionLevel.RECOMMEND: 1,
        ActionLevel.REQUEST_APPROVAL: 2,
        ActionLevel.APPLY: 3,
    }
    return order[normalized]


class Severity(str, Enum):
    """Finding and recommendation severity."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class FindingStatus(str, Enum):
    """Lifecycle status for a patrol finding."""

    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
    SUPERSEDED = "superseded"


class RecommendationStatus(str, Enum):
    """Lifecycle status for a patrol recommendation."""

    PROPOSED = "proposed"
    PUSHED = "pushed"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    EXPIRED = "expired"
    APPLIED = "applied"
    SUPPRESSED = "suppressed"


class RecommendationType(str, Enum):
    """Supported first-pass patrol recommendation types."""

    WAIT = "wait"
    RERUN_SMOKE = "rerun_smoke"
    INSPECT_FAILED_RUN = "inspect_failed_run"
    REQUEST_TRAINING_APPROVAL = "request_training_approval"
    REQUEST_PROMOTION_REVIEW = "request_promotion_review"
    FIX_CONFIG = "fix_config"


class PatrolRunStatus(str, Enum):
    """Top-level status for one patrol round."""

    OK = "ok"
    WARNING = "warning"
    FAILED = "failed"
    NEEDS_APPROVAL = "needs_approval"


class EvidenceSensitivity(str, Enum):
    """Sensitivity level for indexed evidence."""

    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"


class EvidenceRetention(str, Enum):
    """Retention class for indexed evidence."""

    SHORT = "short"
    NORMAL = "normal"
    LONG = "long"


class FailureType(str, Enum):
    """Governable patrol failure taxonomy."""

    TRANSIENT = "transient"
    PERMISSION = "permission"
    EVIDENCE_MISSING = "evidence_missing"
    INCOMPATIBLE = "incompatible"
    DATA_RISK = "data_risk"
    ARTIFACT_RISK = "artifact_risk"
    BUDGET_EXCEEDED = "budget_exceeded"
    POLICY_BLOCKED = "policy_blocked"


class Recoverability(str, Enum):
    """How a patrol failure can be recovered."""

    RETRYABLE = "retryable"
    NEEDS_INPUT = "needs_input"
    BLOCKED = "blocked"
    NON_RETRYABLE = "non_retryable"


class ActionLevel(str, Enum):
    """Patrol action permission levels."""

    INSPECT = "inspect"
    RECOMMEND = "recommend"
    REQUEST_APPROVAL = "request_approval"
    APPLY = "apply"


class RiskLevel(str, Enum):
    """Recommendation or action risk."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RawRef(BaseModel):
    """Reference to raw tool output stored outside the model context."""

    uri: str = Field(..., min_length=1)
    digest: str = Field(..., description="sha256 digest of the raw payload")
    size_bytes: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("digest")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if not SHA256_PATTERN.match(value):
            raise ValueError("digest must match sha256:<64 lowercase hex chars>")
        return value


class EvidenceRecord(BaseModel):
    """Long-lived, traceable evidence entry for findings and recommendations."""

    evidence_id: str
    kind: str
    source_tool: str
    source_uri: str
    raw_ref: RawRef
    summary: str
    created_at: str = Field(default_factory=current_timestamp)
    sensitivity: EvidenceSensitivity = EvidenceSensitivity.INTERNAL
    retention: EvidenceRetention = EvidenceRetention.NORMAL
    digest: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fill_digest(self) -> "EvidenceRecord":
        if self.digest is None:
            self.digest = self.raw_ref.digest
        return self


class Finding(BaseModel):
    """Lifecycle object for an observed patrol risk or opportunity."""

    finding_id: str
    target: Dict[str, Any]
    type: str
    severity: Severity = Severity.INFO
    status: FindingStatus = FindingStatus.OPEN
    summary: str
    evidence_ids: List[str] = Field(default_factory=list)
    fingerprint: Optional[str] = None
    first_seen_at: str = Field(default_factory=current_timestamp)
    last_seen_at: str = Field(default_factory=current_timestamp)
    occurrence_count: int = 1
    resolved_at: Optional[str] = None
    cooldown_until: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fill_fingerprint(self) -> "Finding":
        if self.fingerprint is None:
            self.fingerprint = make_fingerprint(
                {
                    "target": self.target,
                    "type": self.type,
                }
            )
        self.evidence_ids = stable_unique(self.evidence_ids)
        return self


class Recommendation(BaseModel):
    """Governed recommendation that may require approval before execution."""

    recommendation_id: str
    type: RecommendationType
    target: Dict[str, Any]
    severity: Severity = Severity.INFO
    confidence: float = Field(..., ge=0.0, le=1.0)
    finding_ids: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    risk: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    approval_request_id: Optional[str] = None
    cooldown_until: Optional[str] = None
    rollback_plan: Optional[str] = None
    status: RecommendationStatus = RecommendationStatus.PROPOSED
    fingerprint: Optional[str] = None
    created_at: str = Field(default_factory=current_timestamp)
    updated_at: str = Field(default_factory=current_timestamp)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fill_fingerprint(self) -> "Recommendation":
        if self.fingerprint is None:
            self.fingerprint = make_fingerprint(
                {
                    "type": self.type.value if isinstance(self.type, RecommendationType) else self.type,
                    "target": self.target,
                    "risk": self.risk.value if isinstance(self.risk, RiskLevel) else self.risk,
                    "requires_approval": self.requires_approval,
                }
            )
        self.finding_ids = stable_unique(self.finding_ids)
        self.evidence_ids = stable_unique(self.evidence_ids)
        return self

    def validate_governance(self) -> None:
        """Validate evidence and approval boundaries for this recommendation."""
        if self.confidence >= 0.8 and not self.evidence_ids:
            raise ValueError("high-confidence recommendation requires evidence_ids")
        if self.risk == RiskLevel.HIGH and self.type not in {
            RecommendationType.WAIT,
            RecommendationType.INSPECT_FAILED_RUN,
        }:
            if not self.requires_approval:
                raise ValueError("high-risk recommendation must require approval")
        if self.status == RecommendationStatus.APPLIED and self.requires_approval and not self.approval_request_id:
            raise ValueError("applied approval-gated recommendation requires approval_request_id")


class PatrolFailure(BaseModel):
    """A classified failure encountered during a patrol round."""

    failure_id: str
    failure_type: FailureType
    recoverability: Recoverability
    retry_after: Optional[str] = None
    evidence_id: Optional[str] = None
    recommended_next_action: str
    message: str
    created_at: str = Field(default_factory=current_timestamp)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SummaryVersion(BaseModel):
    """Metadata for a compacted patrol memory summary."""

    version: int = 1
    source_patrol_run_ids: List[str] = Field(default_factory=list)
    source: str = "patrol_compaction"
    created_at: str = Field(default_factory=current_timestamp)
    window: Dict[str, Optional[str]] = Field(default_factory=dict)


class AuditEvent(BaseModel):
    """Auditable event emitted by deterministic patrol code."""

    event_type: str
    patrol_run_id: Optional[str] = None
    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    agent_type: Optional[str] = "patrol-push"
    created_at: str = Field(default_factory=current_timestamp)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PatrolMemory(BaseModel):
    """Compacted long-lived patrol memory for one session/project scope."""

    patrol_run_id: str
    project_name: str
    window: Dict[str, Optional[str]] = Field(default_factory=dict)
    summary: str = ""
    summary_version: Optional[SummaryVersion] = None
    open_findings: List[Finding] = Field(default_factory=list)
    closed_findings: List[Finding] = Field(default_factory=list)
    recommendations: List[Recommendation] = Field(default_factory=list)
    approval_requests: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_index: List[EvidenceRecord] = Field(default_factory=list)
    failures: List[PatrolFailure] = Field(default_factory=list)
    next_check_at: Optional[str] = None
    unresolved_errors: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def required_field_values(self) -> Dict[str, Set[str]]:
        """Return required field values that must survive compaction."""
        data = self.model_dump(mode="json")
        values: Dict[str, Set[str]] = {field: set() for field in REQUIRED_PRESERVE_FIELDS}
        _collect_required_values(data, values)
        return {key: found for key, found in values.items() if found}

    def evidence_ids(self) -> Set[str]:
        return {record.evidence_id for record in self.evidence_index}

    def validate_traceability(self) -> None:
        """Ensure live findings, recommendations, and approvals can resolve evidence."""
        evidence_ids = self.evidence_ids()
        for finding in [*self.open_findings, *self.closed_findings]:
            missing = set(finding.evidence_ids) - evidence_ids
            if missing:
                raise ValueError(f"finding {finding.finding_id} references missing evidence: {sorted(missing)}")
        finding_ids = {finding.finding_id for finding in [*self.open_findings, *self.closed_findings]}
        for recommendation in self.recommendations:
            missing_evidence = set(recommendation.evidence_ids) - evidence_ids
            if missing_evidence:
                raise ValueError(
                    f"recommendation {recommendation.recommendation_id} references missing evidence: "
                    f"{sorted(missing_evidence)}"
                )
            missing_findings = set(recommendation.finding_ids) - finding_ids
            if missing_findings:
                raise ValueError(
                    f"recommendation {recommendation.recommendation_id} references missing findings: "
                    f"{sorted(missing_findings)}"
                )
            recommendation.validate_governance()
        for request in self.approval_requests:
            evidence_refs = request.get("evidence_ids", [])
            missing = set(evidence_refs) - evidence_ids
            if missing:
                request_id = request.get("approval_request_id") or request.get("approval_id") or "unknown"
                raise ValueError(f"approval request {request_id} references missing evidence: {sorted(missing)}")


class PatrolRunResult(BaseModel):
    """Deterministic result of one patrol round."""

    patrol_run_id: str
    session_id: str
    status: PatrolRunStatus
    project_scope: List[str] = Field(default_factory=list)
    summary: str
    findings: List[Finding] = Field(default_factory=list)
    recommendations: List[Recommendation] = Field(default_factory=list)
    approval_requests: List[Dict[str, Any]] = Field(default_factory=list)
    evidence: List[EvidenceRecord] = Field(default_factory=list)
    failures: List[PatrolFailure] = Field(default_factory=list)
    budget: Dict[str, Any] = Field(default_factory=dict)
    next_check_at: Optional[str] = None
    state_update: Dict[str, Any] = Field(default_factory=dict)
    audit_events: List[AuditEvent] = Field(default_factory=list)
    created_at: str = Field(default_factory=current_timestamp)

    def validate_traceability(self) -> None:
        """Ensure every finding/recommendation/approval can be traced to evidence."""
        evidence_ids = {record.evidence_id for record in self.evidence}
        finding_ids = {finding.finding_id for finding in self.findings}
        for finding in self.findings:
            missing = set(finding.evidence_ids) - evidence_ids
            if missing:
                raise ValueError(f"finding {finding.finding_id} references missing evidence: {sorted(missing)}")
        for recommendation in self.recommendations:
            missing_evidence = set(recommendation.evidence_ids) - evidence_ids
            if missing_evidence:
                raise ValueError(
                    f"recommendation {recommendation.recommendation_id} references missing evidence: "
                    f"{sorted(missing_evidence)}"
                )
            missing_findings = set(recommendation.finding_ids) - finding_ids
            if missing_findings:
                raise ValueError(
                    f"recommendation {recommendation.recommendation_id} references missing findings: "
                    f"{sorted(missing_findings)}"
                )
            recommendation.validate_governance()
        for request in self.approval_requests:
            evidence_refs = request.get("evidence_ids", [])
            missing = set(evidence_refs) - evidence_ids
            if missing:
                request_id = request.get("approval_request_id") or request.get("approval_id") or "unknown"
                raise ValueError(f"approval request {request_id} references missing evidence: {sorted(missing)}")


def _jsonable(data: Any) -> Any:
    if isinstance(data, BaseModel):
        return data.model_dump(mode="json")
    if isinstance(data, Enum):
        return data.value
    if isinstance(data, dict):
        return {str(key): _jsonable(value) for key, value in data.items()}
    if isinstance(data, (list, tuple, set)):
        return [_jsonable(value) for value in data]
    return data


def _collect_required_values(data: Any, values: Dict[str, Set[str]]) -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            if key in values:
                for string_value in _string_values(value):
                    if string_value:
                        values[key].add(string_value)
            _collect_required_values(value, values)
    elif isinstance(data, list):
        for value in data:
            _collect_required_values(value, values)


def _string_values(value: Any) -> Iterable[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        result: List[str] = []
        for item in value:
            result.extend(_string_values(item))
        return result
    if isinstance(value, dict):
        return []
    return [str(value)]
