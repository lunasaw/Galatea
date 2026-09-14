#!/usr/bin/env python3
"""Export redacted candidate rows into a review-only split package.

The exporter deliberately does not create formal SFT files.  It adds the
session-level split from ``split_manifest.json`` to a copy of each candidate
row and writes an immutable, review-only snapshot.  Rows remain in their
existing review state (normally ``uncertain``); no approval is inferred.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROLES = {"system", "user", "assistant"}
SPLITS = ("train", "validation", "test")


class ExportContractError(RuntimeError):
    """Raised when the review export contract is not satisfied."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - error text is user-facing
        raise ExportContractError(f"invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExportContractError(f"JSON object required: {path}")
    return value


def _ordinary_file(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.is_file() or path.is_symlink():
        raise ExportContractError(f"required ordinary file missing or unsafe: {relative}")
    if root not in path.resolve().parents:
        raise ExportContractError(f"symlink escape: {relative}")
    return path


def split_map(split_manifest: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for split in SPLITS:
        session_ids = split_manifest.get("session_ids_by_split", {}).get(split, [])
        if not isinstance(session_ids, list):
            raise ExportContractError(f"session_ids_by_split.{split} must be a list")
        for session_id in session_ids:
            if not isinstance(session_id, str) or not session_id:
                raise ExportContractError(f"invalid session id in split={split}")
            if session_id in mapping:
                raise ExportContractError(f"session appears in multiple splits: {session_id}")
            mapping[session_id] = split
    if not mapping:
        raise ExportContractError("split manifest contains no sessions")
    return mapping


def validate_candidate(row: dict[str, Any], line_number: int) -> None:
    required = {"sample_id", "session_id", "messages", "metadata"}
    if not required.issubset(row):
        raise ExportContractError(f"candidate line {line_number} missing one of {sorted(required)}")
    if not isinstance(row["sample_id"], str) or not row["sample_id"]:
        raise ExportContractError(f"candidate line {line_number} has invalid sample_id")
    if not isinstance(row["session_id"], str) or not row["session_id"]:
        raise ExportContractError(f"candidate line {line_number} has invalid session_id")
    messages = row["messages"]
    if not isinstance(messages, list) or len(messages) < 2:
        raise ExportContractError(f"candidate line {line_number} has invalid messages")
    for message in messages:
        if not isinstance(message, dict) or set(message) - {"role", "content"}:
            raise ExportContractError(f"candidate line {line_number} has unsupported message fields")
        if message.get("role") not in ROLES or not isinstance(message.get("content"), str):
            raise ExportContractError(f"candidate line {line_number} has invalid message role/content")
        if "text_original_ref" in message:
            raise ExportContractError(f"candidate line {line_number} contains restricted original text reference")
    if messages[-1].get("role") != "assistant":
        raise ExportContractError(f"candidate line {line_number} does not end with assistant target")
    metadata = row["metadata"]
    if not isinstance(metadata, dict):
        raise ExportContractError(f"candidate line {line_number} metadata must be an object")
    review_status = metadata.get("review_status")
    if review_status not in {"keep", "redact_keep", "reject", "uncertain"}:
        raise ExportContractError(f"candidate line {line_number} has invalid review_status={review_status!r}")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    with path.open("wb") as handle:
        for row in rows:
            line = (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            handle.write(line)
            digest.update(line)
            count += 1
    return count, digest.hexdigest()


def export_review_dataset(dataset_root: Path, output_dir: Path | None = None, force_new_run: bool = False) -> Path:
    dataset_root = dataset_root.expanduser().resolve()
    source_manifest_path = _ordinary_file(dataset_root, "manifests/source_manifest.json")
    split_manifest_path = _ordinary_file(dataset_root, "manifests/split_manifest.json")
    candidate_path = _ordinary_file(dataset_root, "work/05_candidates/candidates.jsonl")
    quality_path = _ordinary_file(dataset_root, "reports/quality_report.json")
    source_manifest = read_json(source_manifest_path)
    split_manifest = read_json(split_manifest_path)
    quality_report = read_json(quality_path)
    if quality_report.get("status") != "blocked_for_formal_training":
        raise ExportContractError("review export expects the current formal-training block to remain explicit")
    session_to_split = split_map(split_manifest)

    rows_by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_samples: set[str] = set()
    seen_sessions: dict[str, set[str]] = defaultdict(set)
    status_counts: Counter[str] = Counter()
    with candidate_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ExportContractError(f"invalid candidate JSON at line {line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ExportContractError(f"candidate line {line_number} must be an object")
            validate_candidate(row, line_number)
            sample_id = row["sample_id"]
            session_id = row["session_id"]
            if sample_id in seen_samples:
                raise ExportContractError(f"duplicate sample_id: {sample_id}")
            seen_samples.add(sample_id)
            split = session_to_split.get(session_id)
            if split is None:
                raise ExportContractError(f"candidate session is absent from split manifest: {session_id}")
            review_status = row["metadata"]["review_status"]
            status_counts[review_status] += 1
            copied = json.loads(json.dumps(row, ensure_ascii=False))
            copied_metadata = dict(copied["metadata"])
            copied_metadata["split"] = split
            copied_metadata["export_status"] = "review_only"
            copied_metadata["formal_training_eligible"] = False
            copied["metadata"] = copied_metadata
            rows_by_split[split].append(copied)
            seen_sessions[split].add(session_id)

    if not seen_samples:
        raise ExportContractError("candidate file is empty")

    for split in SPLITS:
        rows_by_split[split].sort(key=lambda row: row["sample_id"])

    if output_dir is None:
        output_dir = dataset_root / "review_exports" / "v1"
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists():
        if not force_new_run:
            raise ExportContractError(f"refusing to overwrite existing review export: {output_dir}")
        suffix = hashlib.sha256(os.urandom(16)).hexdigest()[:12]
        output_dir = output_dir.with_name(f"{output_dir.name}-{suffix}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    staging_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=str(output_dir.parent)))
    try:
        split_counts: dict[str, int] = {}
        split_digests: dict[str, str] = {}
        for split in SPLITS:
            count, digest = _write_jsonl(staging_dir / f"{split}_candidates.jsonl", rows_by_split[split])
            split_counts[split] = count
            split_digests[split] = digest
        manifest = {
            "schema_version": "wechat-review-export-v1",
            "export_status": "review_only",
            "formal_training_eligible": False,
            "blocking_reasons": [
                "manual_review_required",
                "consent_record_not_verified",
                "no_approved_sft_samples",
            ],
            "dataset_id": source_manifest.get("dataset_id"),
            "source_sha256": source_manifest.get("source_sha256"),
            "config_sha256": source_manifest.get("config_sha256"),
            "pipeline_version": source_manifest.get("pipeline_version"),
            "candidate_file_sha256": sha256_file(candidate_path),
            "split_manifest_sha256": sha256_file(split_manifest_path),
            "candidate_count": len(seen_samples),
            "candidate_status_counts": dict(sorted(status_counts.items())),
            "split_counts": split_counts,
            "split_session_counts": {split: len(seen_sessions[split]) for split in SPLITS},
            "split_file_sha256": split_digests,
            "source_review_status": quality_report.get("status"),
            "note": "Rows are redacted candidates for human review; they are not an approved SFT dataset.",
        }
        (staging_dir / "review_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        (staging_dir / "README.md").write_text(
            "# Review-only candidate export\n\n"
            "This snapshot contains redacted candidate rows grouped by the frozen session split. "
            "It is not a formal SFT dataset. Do not train from these files. A reviewer must set "
            "`review_status` to `keep` or `redact_keep`, then the quality, privacy, consent, and "
            "leakage gates must be rerun before writing `datasets/{train,validation,test}.jsonl`.\n",
            encoding="utf-8",
        )
        os.replace(staging_dir, output_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--force-new-run", action="store_true")
    args = parser.parse_args()
    try:
        output = export_review_dataset(args.dataset_root, args.output_dir, args.force_new_run)
    except ExportContractError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(json.dumps({"status": "ok", "export_status": "review_only", "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
