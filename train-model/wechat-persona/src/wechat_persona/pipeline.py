"""Local, consent-gated data engineering pipeline with an explicit write mode."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ._common import digest, parse_datetime
from .consent import verify_consent
from .datasets import build_review_candidates
from .importers import import_messages
from .normalize import normalize_message
from .redact import scan_redacted_text
from .sessionize import deterministic_split, sessionize


class PipelineError(ValueError):
    pass


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


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
    verified = verify_consent(consent, required_purposes={"processing"}, required_message_types={"text"})
    raw_rows, source_manifest = import_messages(source, timezone_name=timezone_name, allowed_root=allowed_root)
    dataset_id = "wechat_" + digest({"source_sha256": source_manifest["source_sha256"], "consent_digest": verified["consent_digest"], "preprocessing": "wechat-persona-data-v1"})[:20]
    plan = {"status": "planned" if not execute else "completed", "dataset_id": dataset_id, "source_sha256": source_manifest["source_sha256"], "consent_digest": verified["consent_digest"], "record_count": len(raw_rows), "will_write": bool(execute), "will_create_mlflow_run": False}
    if not execute:
        return plan
    root = output_root.resolve(strict=False)
    dataset_root = root / dataset_id
    if dataset_root.exists():
        raise FileExistsError("dataset version already exists; refusing overwrite")
    normalized_all: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_rows):
        row = normalize_message(raw, index=index, timezone_name=timezone_name, speaker_map=speaker_map)
        # Keep the consent-routing flag ephemeral; it is removed before any
        # message artifact is written and is not part of message-v1.
        row["_third_party"] = bool(raw.get("third_party", False))
        normalized_all.append(row)
    scope = verified.get("scope") or {}
    scope_start = parse_datetime(scope.get("time_start"), timezone_name) if scope.get("time_start") else None
    scope_end = parse_datetime(scope.get("time_end"), timezone_name) if scope.get("time_end") else None
    allowed_message_types = {str(item).casefold() for item in (scope.get("message_types") or [])}
    allowed_media_types = {str(item).casefold() for item in (scope.get("media_types") or [])}
    filtered: list[dict[str, Any]] = []
    excluded = {"message_type": 0, "time_scope": 0, "third_party": 0}
    for row in normalized_all:
        kind = str(row.get("message_kind") or "").casefold()
        if kind == "text":
            type_allowed = "text" in allowed_message_types
        else:
            type_allowed = kind in allowed_message_types or kind in allowed_media_types
        if not type_allowed:
            excluded["message_type"] += 1
            continue
        timestamp = parse_datetime(row.get("timestamp"), timezone_name)
        if (scope_start and (timestamp is None or timestamp < scope_start)) or (scope_end and (timestamp is None or timestamp > scope_end)):
            excluded["time_scope"] += 1
            continue
        if row.get("_third_party") and scope.get("third_party_policy") == "exclude":
            excluded["third_party"] += 1
            continue
        filtered.append(row)
    # ``third_party`` is an internal consent-routing flag, not part of the
    # public message-v1 artifact schema. Strip it before durable output.
    normalized = [{key: value for key, value in row.items() if key != "_third_party"} for row in filtered]
    leaks = sum(scan_redacted_text(row.get("text_redacted"))["hard_leak_count"] for row in normalized)
    if leaks:
        raise PipelineError("pii_scan_failed")
    retained = [row for row in normalized if row["speaker_role"] in {"self", "target"} and row.get("text_redacted")]
    sessions = sessionize(retained)
    split_manifest = deterministic_split(sessions)
    split_by_session = {sid: split for split, ids in split_manifest["session_ids_by_split"].items() for sid in ids}
    candidates = build_review_candidates(sessions, split_by_session=split_by_session)
    dataset_root.mkdir(parents=True, exist_ok=False)
    _write_jsonl(dataset_root / "redacted/messages.jsonl", normalized)
    _write_jsonl(dataset_root / "sessions/sessions.jsonl", sessions)
    _write_jsonl(dataset_root / "review/candidates.jsonl", candidates)
    lineage: list[dict[str, Any]] = []
    for session in sessions:
        sid = session["session_id"]
        lineage.append({"object_id": sid, "stage": "session", "source_message_ids": session["message_ids"], "source_session_ids": [sid], "consent_scope": "processing", "path": str(dataset_root / "sessions/sessions.jsonl")})
    for row in candidates:
        lineage.append({"object_id": row["sample_id"], "stage": "candidate", "source_message_ids": row["metadata"]["source_message_ids"], "source_session_ids": [row["session_id"]], "consent_scope": "processing", "path": str(dataset_root / "review/candidates.jsonl")})
    _write_jsonl(dataset_root / "manifests/lineage.jsonl", lineage)
    _write_json(dataset_root / "manifests/source_manifest.json", {**source_manifest, "dataset_id": dataset_id, "consent_digest": verified["consent_digest"], "authorization_status": "verified"})
    _write_json(dataset_root / "manifests/split_manifest.json", split_manifest)
    _write_json(dataset_root / "reports/privacy_report.json", {"status": "pass", "hard_leak_count": 0, "message_count": len(normalized), "excluded_by_consent": excluded})
    return {**plan, "dataset_root": dataset_root}
