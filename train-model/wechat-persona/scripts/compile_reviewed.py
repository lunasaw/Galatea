#!/usr/bin/env python3
"""Reconcile redacted candidates and append-only review evidence.

This creates a new reviewed JSONL and never modifies candidates or event logs.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_persona._common import digest, file_digest
from wechat_persona.redact import scan_candidate
from wechat_persona.review import ReviewError, STATUSES, _content_hash


class CompileError(ValueError):
    pass


def _candidate_manifest_digest(rows: list[dict[str, Any]]) -> str:
    return digest({
        "sample_ids": [str(row.get("sample_id") or "") for row in rows],
        "session_ids": [str(row.get("session_id") or "") for row in rows],
        "content_sha256": [_content_hash(row) for row in rows],
    })


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


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompileError(f"cannot read {label}") from exc
    if not isinstance(value, dict):
        raise CompileError(f"{label} must contain an object")
    return value


def _require_string_ids(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise CompileError(f"{label} must be a list of non-empty strings")
    if len(value) != len(set(value)):
        raise CompileError(f"{label} contains duplicate IDs")
    return list(value)


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
    # Scan structured content in order.  This catches credentials split across
    # adjacent turns while excluding role labels, ids and timestamps from the
    # privacy surface.
    if scan_candidate(row)["hard_leak_count"]:
        raise CompileError(f"pii_scan_failed: {row.get('sample_id', '')}")


def compile_reviewed(
    candidates_path: Path,
    events_path: Path,
    output_path: Path,
    reviewed_rows_path: Path | None = None,
    *,
    candidate_manifest_sha256: str | None = None,
    review_summary_path: Path | None = None,
    selection_manifest_path: Path | None = None,
) -> dict[str, Any]:
    if output_path.exists():
        raise FileExistsError("reviewed output already exists; refusing overwrite")
    if review_summary_path is not None and review_summary_path.exists():
        raise FileExistsError("review summary already exists; refusing overwrite")
    candidates = _read_jsonl(candidates_path)
    events = _read_jsonl(events_path)
    candidate_by_id = _assert_unique(candidates, "sample_id", "candidate")
    event_by_id = _assert_unique(events, "sample_id", "review event")
    actual_candidate_manifest_sha256 = _candidate_manifest_digest(candidates)
    if (
        candidate_manifest_sha256 is not None
        and candidate_manifest_sha256 != actual_candidate_manifest_sha256
    ):
        raise CompileError("candidate manifest digest mismatch")
    selected_scope = "all"
    selected_ids = list(candidate_by_id)
    selection_manifest_digest: str | None = None
    selection_version: str | None = None
    selected_counts_by_split: dict[str, int] | None = None
    if selection_manifest_path is not None:
        if candidate_manifest_sha256 is None:
            raise CompileError(
                "subset review requires an explicit candidate manifest digest"
            )
        selection = _read_json(selection_manifest_path, "selection manifest")
        selected_scope = "subset"
        selection_manifest_digest = file_digest(selection_manifest_path)
        selection_version_value = selection.get("selection_version")
        if not isinstance(selection_version_value, str) or not selection_version_value.strip():
            raise CompileError("selection version missing")
        selection_version = selection_version_value
        if not isinstance(selection.get("selection_rule"), str) or not str(
            selection.get("selection_rule")
        ).strip():
            raise CompileError("selection rule missing")
        if selection.get("parent_candidate_manifest_sha256") != actual_candidate_manifest_sha256:
            raise CompileError("selection parent candidate manifest mismatch")
        selected_ids = _require_string_ids(
            selection.get("selected_sample_ids"), "selection selected_sample_ids"
        )
        if any(sample_id not in candidate_by_id for sample_id in selected_ids):
            raise CompileError("selection contains unknown candidate ID")
        if selection.get("selected_sample_ids_sha256") != digest(selected_ids):
            raise CompileError("selection selected IDs digest mismatch")
        if selection.get("selected_count") != len(selected_ids):
            raise CompileError("selection selected_count mismatch")
        selected_counts_by_split = {split: 0 for split in ("train", "validation", "test")}
        for sample_id in selected_ids:
            split = str((candidate_by_id[sample_id].get("metadata") or {}).get("split") or "")
            if split not in selected_counts_by_split:
                raise CompileError(f"candidate split missing: {sample_id}")
            selected_counts_by_split[split] += 1
        if selection.get("split_counts") != selected_counts_by_split:
            raise CompileError("selection split counts mismatch")
        if any(count <= 0 for count in selected_counts_by_split.values()):
            raise CompileError("selection must include every split")

    selected_id_set = set(selected_ids)
    if set(event_by_id) != selected_id_set:
        missing = sorted(selected_id_set - set(event_by_id))
        extra = sorted(set(event_by_id) - selected_id_set)
        raise CompileError(f"candidate/event mismatch: missing={missing[:3]} extra={extra[:3]}")

    reviewed_by_id: dict[str, dict[str, Any]] = {}
    if reviewed_rows_path is not None:
        reviewed_by_id = _assert_unique(_read_jsonl(reviewed_rows_path), "sample_id", "reviewed row")
        extra = sorted(set(reviewed_by_id) - selected_id_set)
        if extra:
            raise CompileError(f"reviewed row has unselected sample_id: {extra[:3]}")

    exported: list[dict[str, Any]] = []
    counts = {status: 0 for status in sorted(STATUSES)}
    for sample_id in selected_ids:
        candidate = candidate_by_id[sample_id]
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
        candidate_hash = _content_hash(candidate)
        if str(event.get("content_sha256") or "") != candidate_hash:
            raise CompileError(f"content hash mismatch: {sample_id}")
        expected_hash = _content_hash(row)
        if status == "redact_keep":
            event_redacted_hash = str(event.get("redacted_content_sha256") or "")
            row_redacted_hash = str((row.get("metadata") or {}).get("redacted_content_sha256") or "")
            if event_redacted_hash != expected_hash or row_redacted_hash != expected_hash:
                raise CompileError(f"redacted content hash mismatch: {sample_id}")
        elif event.get("redacted_content_sha256") not in {None, ""}:
            raise CompileError(f"unexpected redacted content hash: {sample_id}")
        if status in {"keep", "redact_keep"}:
            _scan_messages(row)

        if status in {"keep", "redact_keep"}:
            metadata = dict(candidate.get("metadata") or {})
            review_metadata = dict(row.get("metadata") or {})
            metadata.update({
                "review_status": status,
                "reviewer_id": event["reviewer_id"],
                "reviewed_at": event["reviewed_at"],
                "review_reason": event.get("review_reason"),
                "content_sha256": candidate_hash,
            })
            if status == "redact_keep":
                metadata["redacted_content_sha256"] = review_metadata["redacted_content_sha256"]
            exported.append({**row, "metadata": metadata})

    rejected_counts_by_split = {split: 0 for split in ("train", "validation", "test")}
    for sample_id, event in event_by_id.items():
        if event.get("review_status") == "reject":
            split = str((candidate_by_id[sample_id].get("metadata") or {}).get("split") or "")
            if split not in rejected_counts_by_split:
                raise CompileError(f"candidate split missing: {sample_id}")
            rejected_counts_by_split[split] += 1
    result = {
        "status": "completed",
        "selected_scope": selected_scope,
        "selected_count": len(selected_ids),
        "candidate_count": len(candidates),
        "event_count": len(events),
        "reviewed_count": len(events),
        "exported_count": len(exported),
        "uncertain_count": counts["uncertain"],
        "status_counts": counts,
        "rejected_counts_by_split": rejected_counts_by_split,
        "reviewed_hard_leak_count": 0,
        "human_review_completed": counts["uncertain"] == 0,
        "event_evidence_digest": file_digest(events_path),
        "candidate_manifest_sha256": actual_candidate_manifest_sha256,
        "output": str(output_path),
    }
    if selection_manifest_digest is not None:
        result.update({
            "selection_version": selection_version,
            "selection_manifest_digest": selection_manifest_digest,
            "selected_sample_ids_sha256": digest(selected_ids),
            "selected_counts_by_split": selected_counts_by_split,
        })
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.parent.chmod(stat.S_IRWXU)
    staging_dir = Path(tempfile.mkdtemp(prefix=".review-compile-", dir=output_path.parent))
    staging_dir.chmod(stat.S_IRWXU)
    staged_output = staging_dir / output_path.name
    try:
        with staged_output.open("x", encoding="utf-8") as handle:
            for row in exported:
                handle.write(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
        staged_output.chmod(stat.S_IRUSR | stat.S_IWUSR)
        result["review_evidence_digest"] = file_digest(staged_output)
        staged_summary: Path | None = None
        if review_summary_path is not None:
            if review_summary_path.parent != output_path.parent:
                raise CompileError(
                    "review summary must share the reviewed output directory"
                )
            staged_summary = staging_dir / review_summary_path.name
            with staged_summary.open("x", encoding="utf-8") as handle:
                json.dump(
                    {key: value for key, value in result.items() if key != "output"},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
            staged_summary.chmod(stat.S_IRUSR | stat.S_IWUSR)
        os.link(staged_output, output_path)
        if review_summary_path is not None and staged_summary is not None:
            try:
                os.link(staged_summary, review_summary_path)
            except BaseException:
                output_path.unlink(missing_ok=True)
                raise
            result["review_summary"] = str(review_summary_path)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--reviewed-rows", type=Path, help="separate controlled rows for redact_keep decisions")
    parser.add_argument("--candidate-manifest-sha256")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-summary", type=Path, required=True)
    parser.add_argument("--selection-manifest", type=Path)
    args = parser.parse_args()
    try:
        result = compile_reviewed(
            args.candidates,
            args.events,
            args.output,
            args.reviewed_rows,
            candidate_manifest_sha256=args.candidate_manifest_sha256,
            review_summary_path=args.review_summary,
            selection_manifest_path=args.selection_manifest,
        )
    except (CompileError, FileExistsError, OSError, ReviewError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "will_write_formal_data": False}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps({**result, "will_write_formal_data": False}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
