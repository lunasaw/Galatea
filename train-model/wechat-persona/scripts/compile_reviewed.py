#!/usr/bin/env python3
"""Reconcile redacted candidates and append-only review evidence.

This creates a new reviewed JSONL and never modifies candidates or event logs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_persona.redact import scan_redacted_text
from wechat_persona.review import ReviewError, STATUSES, _content_hash


class CompileError(ValueError):
    pass


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CompileError(f"cannot read {path}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CompileError(f"invalid JSON at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise CompileError(f"JSONL row must be an object at {path}:{line_number}")
        rows.append(value)
    return rows


def _assert_unique(rows: list[dict[str, Any]], field: str, label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(field) or "")
        if not value:
            raise CompileError(f"{label} missing {field}")
        if value in indexed:
            raise CompileError(f"duplicate {label} {field}: {value}")
        indexed[value] = row
    return indexed


def _scan_messages(row: dict[str, Any]) -> None:
    for message in row.get("messages") or []:
        if scan_redacted_text(str(message.get("content") or ""))["hard_leak_count"]:
            raise CompileError(f"pii_scan_failed: {row.get('sample_id', '')}")


def compile_reviewed(
    candidates_path: Path,
    events_path: Path,
    output_path: Path,
    reviewed_rows_path: Path | None = None,
) -> dict[str, Any]:
    candidates = _read_jsonl(candidates_path)
    events = _read_jsonl(events_path)
    candidate_by_id = _assert_unique(candidates, "sample_id", "candidate")
    event_by_id = _assert_unique(events, "sample_id", "review event")
    if set(candidate_by_id) != set(event_by_id):
        missing = sorted(set(candidate_by_id) - set(event_by_id))
        extra = sorted(set(event_by_id) - set(candidate_by_id))
        raise CompileError(f"candidate/event mismatch: missing={missing[:3]} extra={extra[:3]}")

    reviewed_by_id: dict[str, dict[str, Any]] = {}
    if reviewed_rows_path is not None:
        reviewed_by_id = _assert_unique(_read_jsonl(reviewed_rows_path), "sample_id", "reviewed row")
        extra = sorted(set(reviewed_by_id) - set(candidate_by_id))
        if extra:
            raise CompileError(f"reviewed row has unknown sample_id: {extra[:3]}")

    exported: list[dict[str, Any]] = []
    counts = {status: 0 for status in sorted(STATUSES)}
    for sample_id, candidate in candidate_by_id.items():
        event = event_by_id[sample_id]
        status = str(event.get("review_status") or "")
        if status not in STATUSES:
            raise CompileError(f"invalid review status: {sample_id}")
        counts[status] += 1
        if str(event.get("session_id") or "") != str(candidate.get("session_id") or ""):
            raise CompileError(f"session mismatch: {sample_id}")
        if not event.get("reviewer_id") or not event.get("reviewed_at"):
            raise CompileError(f"review evidence incomplete: {sample_id}")

        row = candidate
        if status == "redact_keep":
            row = reviewed_by_id.get(sample_id)
            if row is None:
                raise CompileError(f"redact_keep requires a reviewed row: {sample_id}")
            if str(row.get("session_id") or "") != str(candidate.get("session_id") or ""):
                raise CompileError(f"reviewed row session mismatch: {sample_id}")
        elif status in {"keep", "reject"} and sample_id in reviewed_by_id:
            raise CompileError(f"unexpected reviewed row for {status}: {sample_id}")

        if status in {"redact_keep", "reject"} and not event.get("review_reason"):
            raise CompileError(f"review reason missing: {sample_id}")
        if status == "redact_keep" and not (row.get("metadata") or {}).get("redacted_content_sha256"):
            raise CompileError(f"redacted content hash missing: {sample_id}")
        expected_hash = _content_hash(row)
        if str(event.get("content_sha256") or "") != expected_hash:
            raise CompileError(f"content hash mismatch: {sample_id}")
        _scan_messages(row)

        if status in {"keep", "redact_keep"}:
            metadata = dict(candidate.get("metadata") or {})
            review_metadata = dict(row.get("metadata") or {})
            metadata.update({
                "review_status": status,
                "reviewer_id": event["reviewer_id"],
                "reviewed_at": event["reviewed_at"],
                "review_reason": event.get("review_reason"),
                "content_sha256": expected_hash,
            })
            if status == "redact_keep":
                metadata["redacted_content_sha256"] = review_metadata["redacted_content_sha256"]
            exported.append({**row, "metadata": metadata})

    if output_path.exists():
        raise FileExistsError("reviewed output already exists; refusing overwrite")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        for row in exported:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return {"status": "completed", "candidate_count": len(candidates), "reviewed_count": len(events), "exported_count": len(exported), "status_counts": counts, "output": str(output_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--reviewed-rows", type=Path, help="separate controlled rows for redact_keep decisions")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = compile_reviewed(args.candidates, args.events, args.output, args.reviewed_rows)
    except (CompileError, FileExistsError, OSError, ReviewError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "will_write_formal_data": False}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps({**result, "will_write_formal_data": False}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
