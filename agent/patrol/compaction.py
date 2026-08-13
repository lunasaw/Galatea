"""Patrol memory compaction with deterministic fidelity checks."""

from __future__ import annotations

import re
from typing import Iterable

from agent.schemas.patrol import PatrolMemory, REQUIRED_PRESERVE_FIELDS, SummaryVersion

SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(token\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(password\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(secret\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+"),
]


def compact_patrol_memory(memory: PatrolMemory, max_summary_chars: int = 2000) -> PatrolMemory:
    """Return a compacted, redacted memory view while preserving structured state."""
    compacted = memory.model_copy(deep=True)
    compacted.summary = _truncate(_redact(_build_summary(memory)), max_summary_chars)
    compacted.unresolved_errors = [_redact(error) for error in memory.unresolved_errors]
    compacted.metadata = _redact_metadata(memory.metadata)
    for evidence in compacted.evidence_index:
        evidence.summary = _truncate(_redact(evidence.summary), 600)
    for finding in [*compacted.open_findings, *compacted.closed_findings]:
        finding.summary = _truncate(_redact(finding.summary), 600)
    for recommendation in compacted.recommendations:
        if recommendation.rollback_plan:
            recommendation.rollback_plan = _truncate(_redact(recommendation.rollback_plan), 600)
    compacted.summary_version = SummaryVersion(
        version=(memory.summary_version.version + 1 if memory.summary_version else 1),
        source_patrol_run_ids=[memory.patrol_run_id],
        window=memory.window,
    )
    validate_compaction_fidelity(memory, compacted)
    return compacted


def validate_compaction_fidelity(original: PatrolMemory, compacted: PatrolMemory) -> None:
    """Raise if compaction drops IDs, evidence links, approvals, or errors."""
    original_values = original.required_field_values()
    compacted_values = compacted.required_field_values()
    for field in REQUIRED_PRESERVE_FIELDS:
        if field == "unresolved_errors":
            if original.unresolved_errors and len(compacted.unresolved_errors) < len(original.unresolved_errors):
                raise ValueError("compaction dropped unresolved errors")
            continue
        missing = original_values.get(field, set()) - compacted_values.get(field, set())
        if missing:
            raise ValueError(f"compaction dropped required {field}: {sorted(missing)}")

    compacted.validate_traceability()
    if original.open_findings and not compacted.open_findings:
        raise ValueError("compaction dropped open findings")
    if original.approval_requests and not compacted.approval_requests:
        raise ValueError("compaction dropped approval requests")
    if _contains_sensitive_text(compacted.summary):
        raise ValueError("compacted summary contains sensitive content")


def redact_sensitive_text(value: str) -> str:
    """Public helper for redacting common credential patterns."""
    return _redact(value)


def _build_summary(memory: PatrolMemory) -> str:
    parts = [
        f"Patrol run: {memory.patrol_run_id}",
        f"Project: {memory.project_name}",
    ]
    if memory.window:
        parts.append(f"Window: {memory.window.get('started_at')} -> {memory.window.get('ended_at')}")
    if memory.summary:
        parts.append(f"Summary: {memory.summary}")
    if memory.open_findings:
        findings = "; ".join(f"{finding.finding_id}: {finding.summary}" for finding in memory.open_findings)
        parts.append(f"Open findings: {findings}")
    if memory.recommendations:
        recommendations = "; ".join(
            f"{recommendation.recommendation_id}: {recommendation.type}" for recommendation in memory.recommendations
        )
        parts.append(f"Recommendations: {recommendations}")
    if memory.approval_requests:
        approvals = ", ".join(
            str(request.get("approval_request_id") or request.get("approval_id") or "unknown")
            for request in memory.approval_requests
        )
        parts.append(f"Approval requests: {approvals}")
    if memory.evidence_index:
        evidence = "; ".join(f"{record.evidence_id}: {record.summary}" for record in memory.evidence_index)
        parts.append(f"Evidence index: {evidence}")
    if memory.unresolved_errors:
        parts.append("Unresolved errors: " + "; ".join(memory.unresolved_errors))
    if memory.next_check_at:
        parts.append(f"Next check: {memory.next_check_at}")
    return "\n".join(parts)


def _redact(value: str) -> str:
    redacted = value
    for pattern in SENSITIVE_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", redacted)
    return redacted


def _redact_metadata(metadata: dict) -> dict:
    redacted = {}
    for key, value in metadata.items():
        if isinstance(value, str):
            if _looks_like_long_log(value):
                redacted[key] = f"<omitted {len(value)} chars>"
            else:
                redacted[key] = _redact(value)
        else:
            redacted[key] = value
    return redacted


def _truncate(value: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(value) <= max_chars:
        return value
    marker = "...[truncated]"
    keep = max(max_chars - len(marker), 0)
    return value[:keep] + marker


def _contains_sensitive_text(value: str) -> bool:
    return any(pattern.search(value) for pattern in SENSITIVE_PATTERNS)


def _looks_like_long_log(value: str) -> bool:
    return len(value) > 1000 or value.count("\n") > 40
