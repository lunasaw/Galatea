"""Tool output envelope helpers for patrol-safe model context."""

from __future__ import annotations

from typing import Any, Dict, Optional

from agent.patrol.compaction import redact_sensitive_text
from agent.schemas.patrol import EvidenceRecord, RawRef, sha256_digest


def build_tool_envelope(
    *,
    source_tool: str,
    payload: Any,
    kind: str,
    source_uri: str,
    raw_uri: str,
    summary: str,
    max_summary_chars: int = 1000,
    sensitivity: str = "internal",
    retention: str = "normal",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a safe ``summary + evidence + raw_ref`` envelope around raw output."""
    digest = sha256_digest(payload)
    summary_for_model = _truncate(redact_sensitive_text(summary), max_summary_chars)
    raw_ref = RawRef(uri=raw_uri, digest=digest, size_bytes=len(str(payload).encode("utf-8")))
    evidence = EvidenceRecord(
        evidence_id="ev_" + digest.removeprefix("sha256:")[:16],
        kind=kind,
        source_tool=source_tool,
        source_uri=source_uri,
        raw_ref=raw_ref,
        summary=summary_for_model,
        sensitivity=sensitivity,
        retention=retention,
        metadata=metadata or {},
    )
    return {
        "summary_for_model": summary_for_model,
        "evidence": [evidence.model_dump(mode="json")],
        "raw_ref": raw_ref.model_dump(mode="json"),
        "legacy_payload": payload,
    }


def summarize_payload(payload: Any, *, subject: str, max_chars: int = 1000) -> str:
    """Create a compact human-readable summary from a JSON-like payload."""
    if isinstance(payload, dict):
        if payload.get("error"):
            text = f"{subject}: error={payload.get('error')}"
        elif "status" in payload:
            text = f"{subject}: status={payload.get('status')}"
        elif "is_available" in payload:
            text = f"{subject}: is_available={payload.get('is_available')}"
        elif "count" in payload:
            text = f"{subject}: count={payload.get('count')}"
        else:
            keys = ", ".join(sorted(str(key) for key in payload.keys())[:8])
            text = f"{subject}: keys={keys}"
    else:
        text = f"{subject}: {payload}"
    return _truncate(redact_sensitive_text(text), max_chars)


def _truncate(value: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(value) <= max_chars:
        return value
    marker = "...[truncated]"
    keep = max(max_chars - len(marker), 0)
    return value[:keep] + marker
