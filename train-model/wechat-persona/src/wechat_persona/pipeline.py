"""Local, consent-gated data engineering pipeline with an explicit write mode.

The pipeline writes a review-only derived dataset. Its source manifest is an
identity record rather than a copy of the consent ledger or source content:
only digests, counts, versions, and aggregate privacy results are persisted.
"""
from __future__ import annotations

import json
from pathlib import Path
import stat
from typing import Any, Mapping

from . import redact as redact_module
from ._common import digest, parse_datetime
from .consent import consent_file_digest, verify_consent
from .datasets import PRIVACY_BOUNDARY_VERSION, build_review_candidates
from .importers import import_messages
from .normalize import NORMALIZATION_VERSION, normalize_message
from .redact import (
    count_adjacent_secret_redactions,
    redact_adjacent_messages,
    scan_adjacent_messages,
    scan_candidate,
    scan_session,
)
from .sessionize import SESSION_VERSION, deterministic_split, sessionize


class PipelineError(ValueError):
    pass


PIPELINE_VERSION = "wechat-persona-data-v2"
MANIFEST_SCHEMA_VERSION = "wechat-source-manifest-v2"


def build_dataset_id(
    *,
    source_sha256: str,
    consent_digest: str,
    preprocessing_version: str,
    redaction_version: str,
    scanner_version: str,
    selection_version: str | None = None,
    selection_manifest_digest: str | None = None,
) -> str:
    """Derive identity from every data-affecting version."""
    identity = {
        "source_sha256": str(source_sha256),
        "consent_digest": str(consent_digest),
        "preprocessing_version": str(preprocessing_version),
        "redaction_version": str(redaction_version),
        "scanner_version": str(scanner_version),
    }
    if bool(selection_version) != bool(selection_manifest_digest):
        raise PipelineError(
            "selection_version and selection_manifest_digest must be provided together"
        )
    if selection_manifest_digest:
        identity["selection_version"] = str(selection_version)
        identity["selection_manifest_digest"] = str(selection_manifest_digest)
    return "wechat_" + digest(identity)[:20]


def _count_duplicate_ids(rows: list[dict[str, Any]]) -> int:
    counts: dict[str, int] = {}
    for row in rows:
        message_id = str(row.get("message_id") or "")
        if message_id:
            counts[message_id] = counts.get(message_id, 0) + 1
    return sum(count - 1 for count in counts.values() if count > 1)


def _is_explicit_third_party(row: Mapping[str, Any]) -> bool:
    """Return whether the importer explicitly classified the source as third party."""

    value = row.get("third_party")
    return value is True or (
        isinstance(value, str) and value.strip().casefold() in {"true", "1", "yes"}
    )


def _aggregate_privacy(reports: list[Mapping[str, Any]]) -> dict[str, int]:
    """Sum numeric scanner fields without ever retaining matched content."""

    aggregate: dict[str, int] = {}
    for report in reports:
        for key, value in report.items():
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            aggregate[key] = aggregate.get(key, 0) + value
    aggregate.setdefault("hard_leak_count", 0)
    aggregate.setdefault("cross_message_secret_matches", 0)
    return aggregate


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(stat.S_IRWXU)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(stat.S_IRWXU)
    with path.open("x", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _prepare_import(
    *,
    source: Path,
    consent: Path,
    allowed_root: Path,
    speaker_map: Mapping[str, str],
    timezone_name: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Build all derived import objects in memory without writing anything.

    This is the authoritative preflight path.  The write-enabled pipeline and
    the CLI's ``--check`` mode both call it, so a check cannot silently skip a
    privacy or identity gate that execution would later encounter.
    """
    if set(speaker_map.values()) != {"self", "target"} or len(speaker_map) != 2:
        raise PipelineError("speaker_map must bind exactly one self and one target")
    verified = verify_consent(
        consent,
        required_purposes={"processing", "persona_style"},
        required_message_types={"text"},
    )
    raw_rows, source_manifest = import_messages(
        source, timezone_name=timezone_name, allowed_root=allowed_root
    )
    preprocessing_version = "+".join((
        PIPELINE_VERSION,
        NORMALIZATION_VERSION,
        SESSION_VERSION,
        PRIVACY_BOUNDARY_VERSION,
    ))
    redaction_version = str(redact_module.REDACTION_VERSION)
    scanner_version = str(getattr(redact_module, "PRIVACY_SCANNER_VERSION", "unknown"))
    dataset_id = build_dataset_id(
        source_sha256=source_manifest["source_sha256"],
        consent_digest=verified["consent_digest"],
        preprocessing_version=preprocessing_version,
        redaction_version=redaction_version,
        scanner_version=scanner_version,
    )
    consent_file_sha256 = verified.get("consent_file_sha256") or consent_file_digest(Path(consent))

    normalized_all: list[dict[str, Any]] = []
    timestamp_parse_failure_count = 0
    for index, raw in enumerate(raw_rows):
        row = normalize_message(raw, index=index, timezone_name=timezone_name, speaker_map=speaker_map)
        # This routing flag is intentionally ephemeral and is never emitted.
        row["_third_party"] = _is_explicit_third_party(raw)
        normalized_all.append(row)
        if row.get("timestamp") is None:
            timestamp_parse_failure_count += 1
    duplicate_message_id_count = _count_duplicate_ids(normalized_all)
    unknown_role_count = sum(row.get("speaker_role") == "unknown" for row in normalized_all)

    scope = verified.get("scope") or {}
    scope_start = parse_datetime(scope.get("time_start"), timezone_name) if scope.get("time_start") else None
    scope_end = parse_datetime(scope.get("time_end"), timezone_name) if scope.get("time_end") else None
    allowed_message_types = {str(item).casefold() for item in (scope.get("message_types") or [])}
    allowed_media_types = {str(item).casefold() for item in (scope.get("media_types") or [])}
    filtered: list[dict[str, Any]] = []
    excluded = {"message_type": 0, "time_scope": 0, "third_party": 0}
    for row in normalized_all:
        kind = str(row.get("message_kind") or "").casefold()
        type_allowed = "text" in allowed_message_types if kind == "text" else (
            kind in allowed_message_types or kind in allowed_media_types
        )
        if not type_allowed:
            excluded["message_type"] += 1
            continue
        timestamp = parse_datetime(row.get("timestamp"), timezone_name)
        if ((scope_start and (timestamp is None or timestamp < scope_start)) or
                (scope_end and (timestamp is None or timestamp > scope_end))):
            excluded["time_scope"] += 1
            continue
        if row.get("_third_party") and scope.get("third_party_policy") == "exclude":
            excluded["third_party"] += 1
            continue
        filtered.append(row)
    normalized = [{key: value for key, value in row.items() if key != "_third_party"} for row in filtered]
    # Apply the sequence-level credential pass while source indices are still
    # attached.  A consent-filtered source record therefore cannot make two
    # unrelated retained messages appear adjacent.
    cross_message_secret_redaction_count = count_adjacent_secret_redactions(normalized)
    normalized = redact_adjacent_messages(normalized)

    messages_privacy = scan_adjacent_messages([
        {
            "content": row.get("text_redacted"),
            "source_record_index": row.get("source_record_index"),
        }
        for row in normalized
        if row.get("speaker_role") in {"self", "target"}
    ])
    if messages_privacy.get("hard_leak_count", 0):
        raise PipelineError("pii_scan_failed: messages")
    if unknown_role_count:
        raise PipelineError("unknown_role")
    if duplicate_message_id_count:
        raise PipelineError("duplicate_message_id")
    if timestamp_parse_failure_count:
        raise PipelineError("timestamp_parse_failure")

    retained = [row for row in normalized if row["speaker_role"] in {"self", "target"} and row.get("text_redacted")]
    sessions = sessionize(retained)
    sessions_privacy = _aggregate_privacy([scan_session(session) for session in sessions])
    if sessions_privacy.get("hard_leak_count", 0):
        raise PipelineError("pii_scan_failed: sessions")
    split_manifest = deterministic_split(sessions)
    split_by_session = {sid: split for split, ids in split_manifest["session_ids_by_split"].items() for sid in ids}
    candidates = build_review_candidates(sessions, split_by_session=split_by_session)
    candidates_privacy = _aggregate_privacy([scan_candidate(candidate) for candidate in candidates])
    if candidates_privacy.get("hard_leak_count", 0):
        raise PipelineError("pii_scan_failed: candidates")
    candidate_manifest_sha256 = digest({
        "sample_ids": [str(candidate.get("sample_id") or "") for candidate in candidates],
        "session_ids": [str(candidate.get("session_id") or "") for candidate in candidates],
        "content_sha256": [
            str((candidate.get("metadata") or {}).get("content_sha256") or "")
            for candidate in candidates
        ],
    })
    split_manifest = {
        **split_manifest,
        "candidate_manifest_sha256": candidate_manifest_sha256,
        "candidate_counts": {
            split: sum(1 for candidate in candidates if candidate.get("metadata", {}).get("split") == split)
            for split in ("train", "validation", "test")
        },
    }
    split_manifest["manifest_sha256"] = digest({
        key: value for key, value in split_manifest.items()
        if key not in {"split_sha256", "manifest_sha256"}
    })
    source_manifest_value = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        **source_manifest,
        "dataset_id": dataset_id,
        "consent_id": verified["consent_id"],
        "consent_file_sha256": consent_file_sha256,
        "consent_digest": verified["consent_digest"],
        "authorization_status": "verified",
        "raw_message_count": len(raw_rows),
        "filtered_message_count": len(normalized),
        "retained_self_target_text_count": len(retained),
        "excluded_by_consent": excluded,
        "unknown_role_count": unknown_role_count,
        "duplicate_message_id_count": duplicate_message_id_count,
        "timestamp_parse_failure_count": timestamp_parse_failure_count,
        "preprocessing_version": preprocessing_version,
        "redaction_version": redaction_version,
        "scanner_version": scanner_version,
        "privacy_counts": {
            "messages": messages_privacy,
            "sessions": sessions_privacy,
            "candidates": candidates_privacy,
        },
        "redaction_counts": {
            "cross_message_secret_redactions": cross_message_secret_redaction_count,
        },
        "split_sha256": split_manifest["split_sha256"],
        "candidate_manifest_sha256": candidate_manifest_sha256,
    }
    source_manifest_value["manifest_sha256"] = digest({
        key: value for key, value in source_manifest_value.items() if key != "manifest_sha256"
    })
    plan = {
        "status": "planned",
        "dataset_id": dataset_id,
        "source_sha256": source_manifest["source_sha256"],
        "source_size_bytes": source_manifest.get("source_size_bytes"),
        "consent_id": verified["consent_id"],
        "consent_digest": verified["consent_digest"],
        "consent_file_sha256": consent_file_sha256,
        "raw_message_count": len(raw_rows),
        "record_count": len(raw_rows),
        "filtered_message_count": len(normalized),
        "retained_self_target_text_count": len(retained),
        "excluded_by_consent": excluded,
        "unknown_role_count": unknown_role_count,
        "duplicate_message_id_count": duplicate_message_id_count,
        "timestamp_parse_failure_count": timestamp_parse_failure_count,
        "preprocessing_version": preprocessing_version,
        "redaction_version": redaction_version,
        "scanner_version": scanner_version,
        "privacy_counts": source_manifest_value["privacy_counts"],
        "redaction_counts": source_manifest_value["redaction_counts"],
        "manifest_sha256": source_manifest_value["manifest_sha256"],
        "split_sha256": split_manifest["split_sha256"],
        "split_manifest_sha256": split_manifest["manifest_sha256"],
        "candidate_manifest_sha256": candidate_manifest_sha256,
        "session_counts": split_manifest.get("session_counts", {}),
        "candidate_counts": split_manifest.get("candidate_counts", {}),
        "will_write": False,
        "will_create_mlflow_run": False,
    }
    return plan, normalized, sessions, candidates, source_manifest_value, split_manifest


def run_import_pipeline(
    *,
    source: Path,
    consent: Path,
    output_root: Path,
    allowed_root: Path,
    speaker_map: Mapping[str, str],
    timezone_name: str = "Asia/Shanghai",
    execute: bool = False,
) -> dict[str, Any]:
    plan, normalized, sessions, candidates, source_manifest_value, split_manifest = _prepare_import(
        source=source,
        consent=consent,
        allowed_root=allowed_root,
        speaker_map=speaker_map,
        timezone_name=timezone_name,
    )
    if not execute:
        return plan
    root = output_root.resolve(strict=False)
    dataset_root = root / str(plan["dataset_id"])
    if dataset_root.exists():
        raise FileExistsError("dataset version already exists; refusing overwrite")
    dataset_root.mkdir(parents=True, exist_ok=False)
    dataset_root.chmod(stat.S_IRWXU)
    _write_jsonl(dataset_root / "redacted/messages.jsonl", normalized)
    _write_jsonl(dataset_root / "sessions/sessions.jsonl", sessions)
    _write_jsonl(dataset_root / "review/candidates.jsonl", candidates)
    lineage: list[dict[str, Any]] = []
    for session in sessions:
        sid = session["session_id"]
        lineage.append({
            "object_id": sid,
            "stage": "session",
            "source_message_ids": session["message_ids"],
            "source_session_ids": [sid],
            "consent_scope": "processing+persona_style",
            "path": str(dataset_root / "sessions/sessions.jsonl"),
        })
    for row in candidates:
        metadata = row["metadata"]
        lineage.append({
            "object_id": row["sample_id"],
            "stage": "candidate",
            "source_message_ids": list(dict.fromkeys([
                *metadata["context_message_ids"],
                *metadata["source_message_ids"],
            ])),
            "target_message_ids": metadata["source_message_ids"],
            "context_message_ids": metadata["context_message_ids"],
            "source_session_ids": [row["session_id"]],
            "consent_scope": "processing+persona_style",
            "path": str(dataset_root / "review/candidates.jsonl"),
        })
    _write_jsonl(dataset_root / "manifests/lineage.jsonl", lineage)
    _write_json(dataset_root / "manifests/source_manifest.json", source_manifest_value)
    _write_json(dataset_root / "manifests/split_manifest.json", split_manifest)
    _write_json(dataset_root / "reports/privacy_report.json", {
        "schema_version": "wechat-privacy-report-v2",
        "status": "pass",
        "hard_leak_count": 0,
        "layers": {
            "messages": source_manifest_value["privacy_counts"]["messages"],
            "sessions": source_manifest_value["privacy_counts"]["sessions"],
            "candidates": source_manifest_value["privacy_counts"]["candidates"],
        },
        "message_count": plan["filtered_message_count"],
        "session_count": len(sessions),
        "candidate_count": len(candidates),
        "excluded_by_consent": plan["excluded_by_consent"],
        "scanner_version": plan["scanner_version"],
        "redaction_counts": plan["redaction_counts"],
    })
    return {
        **plan,
        "status": "completed",
        "will_write": True,
        "dataset_root": dataset_root,
    }
