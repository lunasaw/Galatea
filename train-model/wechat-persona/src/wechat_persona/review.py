"""Append-only human review transitions over redacted candidates."""
from __future__ import annotations

import hashlib
import json
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .redact import normalize_text


class ReviewError(ValueError): pass
STATUSES = {"keep", "redact_keep", "reject", "uncertain"}


def _content_hash(row: dict[str, Any]) -> str:
    content = "\n".join(
        normalize_text(item.get("content", ""))
        for item in row.get("messages", [])
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def apply_review(row: dict[str, Any], status: str, *, reviewer_id: str | None = None, reason: str | None = None, edited_messages: list[dict[str, Any]] | None = None, reviewed_at: str | None = None) -> dict[str, Any]:
    if status not in STATUSES: raise ReviewError("invalid review status")
    if not reviewer_id: raise ReviewError("reviewer_id is required")
    result = {**row, "metadata": {**dict(row.get("metadata") or {})}}
    if edited_messages is not None:
        result["messages"] = edited_messages
        # The source-boundary view describes the original candidate. Once a
        # reviewer changes content it can no longer be authoritative; dropping
        # it forces every later gate to scan the edited structured messages.
        result.pop("privacy_scan_messages", None)
        result.pop("privacy_boundary_version", None)
    meta = result["metadata"]; meta.update({"review_status": status, "reviewer_id": reviewer_id, "reviewed_at": reviewed_at or datetime.now(timezone.utc).isoformat(), "review_reason": reason, "content_sha256": _content_hash(row)})
    if status == "redact_keep":
        if not edited_messages: raise ReviewError("redact_keep requires edited_messages")
        if not reason: raise ReviewError("redact_keep requires a reason")
        meta["redacted_content_sha256"] = _content_hash(result)
    if status == "reject" and not reason:
        raise ReviewError("reject requires a classified reason")
    return result


def export_approved(rows: Iterable[dict[str, Any]], *, split: str) -> list[dict[str, Any]]:
    if split not in {"train", "validation", "test"}: raise ReviewError("invalid split")
    result = []
    for row in rows:
        meta = dict(row.get("metadata") or {})
        if meta.get("review_status") not in {"keep", "redact_keep"}: continue
        if not meta.get("reviewer_id") or not meta.get("reviewed_at"): raise ReviewError("approved row is not fully reviewed")
        copy = {**row, "metadata": {**meta, "split": split}}
        result.append(copy)
    return result


def append_review_event(path: Path, reviewed_row: dict[str, Any]) -> dict[str, Any]:
    """Append an ID/hash-only review event; candidate text is never logged."""
    meta = dict(reviewed_row.get("metadata") or {})
    if meta.get("review_status") not in STATUSES or not meta.get("reviewer_id"):
        raise ReviewError("review event requires a completed decision")
    event = {
        "sample_id": str(reviewed_row.get("sample_id", "")),
        "session_id": str(reviewed_row.get("session_id", "")),
        "review_status": meta["review_status"],
        "reviewer_id": meta["reviewer_id"],
        "reviewed_at": meta.get("reviewed_at"),
        "review_reason": meta.get("review_reason"),
        # ``content_sha256`` always binds the unedited candidate content.
        # ``redacted_content_sha256`` separately binds edited redact_keep
        # content; keeping both prevents a later compiler from conflating the
        # two identities.
        "content_sha256": meta.get("content_sha256") or _content_hash(reviewed_row),
        "redacted_content_sha256": meta.get("redacted_content_sha256"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(stat.S_IRWXU)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return event


def append_reviewed_row(path: Path, reviewed_row: dict[str, Any]) -> None:
    """Persist the redacted reviewed row separately from the hash-only event log.

    The event log intentionally contains no message text.  This companion file is
    the controlled content record needed to reconstruct ``reviewed-jsonl`` for
    ``redact_keep`` decisions; it must never be the original candidates file.
    """
    meta = dict(reviewed_row.get("metadata") or {})
    if meta.get("review_status") not in STATUSES or not meta.get("reviewer_id"):
        raise ReviewError("reviewed row requires a completed decision")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(stat.S_IRWXU)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(reviewed_row, ensure_ascii=False, separators=(",", ":")) + "\n")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
