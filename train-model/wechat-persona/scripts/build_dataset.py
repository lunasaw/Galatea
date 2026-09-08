#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_persona._common import digest, file_digest
from wechat_persona.datasets import DatasetError, build_sft_splits, write_sft_snapshot
from wechat_persona.runtime import load_project_config, validate_project_config


_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_IDENTITY_FIELDS = (
    "dataset_id",
    "source_sha256",
    "manifest_sha256",
    "split_sha256",
    "consent_digest",
    "preprocessing_version",
    "redaction_version",
    "scanner_version",
    "review_evidence_digest",
)
_SPLITS = ("train", "validation", "test")


def _read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetError(f"{label} must be readable UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise DatasetError(f"{label} must contain an object")
    return value


def _read_jsonl(path: Path, label: str) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise DatasetError(f"{label} must be readable UTF-8 JSONL") from exc
    rows = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"invalid JSON at {label}:{line_number}") from exc
        if not isinstance(value, dict):
            raise DatasetError(f"{label} row must be an object")
        rows.append(value)
    return rows


def _require_digest(value: object, field: str) -> str:
    text = str(value or "")
    if not _DIGEST_RE.fullmatch(text) or text == "0" * 64:
        raise DatasetError(f"{field} must be a non-placeholder lowercase SHA-256 digest")
    return text


def _manifest_digest(value: dict) -> str:
    return digest({key: child for key, child in value.items() if key != "manifest_sha256"})


def _require_string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise DatasetError(f"{field} must be a list of non-empty strings")
    if len(value) != len(set(value)):
        raise DatasetError(f"{field} contains duplicate IDs")
    return list(value)


def _sample_ids_by_split(rows: list[dict]) -> dict[str, list[str]]:
    result = {split: [] for split in _SPLITS}
    for row in rows:
        split = str((row.get("metadata") or {}).get("split") or "")
        if split not in result:
            raise DatasetError("reviewed row split is invalid")
        result[split].append(str(row.get("sample_id") or ""))
    return result


def _privacy_zero(report: dict, layer: str) -> None:
    layers = report.get("layers") or report.get("privacy_counts") or {}
    value = layers.get(layer) if isinstance(layers, dict) else None
    hard_leak_count = value.get("hard_leak_count") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or isinstance(hard_leak_count, bool)
        or not isinstance(hard_leak_count, int)
        or hard_leak_count != 0
    ):
        raise DatasetError(f"privacy evidence missing or blocked: {layer}")


def _require_exact_zero(value: object, field: str) -> None:
    """Require the integer zero; booleans must not satisfy numeric gates."""

    if isinstance(value, bool) or not isinstance(value, int) or value != 0:
        raise DatasetError(f"{field} must be integer zero")


def _validate_lineage(rows: list[dict], lineage: list[dict]) -> None:
    by_object: dict[str, list[dict]] = {}
    for item in lineage:
        object_id = str(item.get("object_id") or "")
        if object_id:
            by_object.setdefault(object_id, []).append(item)
    for row in rows:
        sample_id = str(row.get("sample_id") or "")
        session_id = str(row.get("session_id") or "")
        metadata = row.get("metadata") or {}
        target_message_ids = set(
            _require_string_list(
                metadata.get("source_message_ids"),
                f"reviewed row {sample_id} source_message_ids",
            )
        )
        context_message_ids = set(
            _require_string_list(
                metadata.get("context_message_ids"),
                f"reviewed row {sample_id} context_message_ids",
            )
        )
        expected_message_ids = target_message_ids | context_message_ids
        if target_message_ids & context_message_ids:
            raise DatasetError(
                f"reviewed row target/context lineage overlaps: {sample_id}"
            )
        entries = by_object.get(sample_id, [])
        if not any(
            entry.get("stage") == "candidate"
            and session_id in set(entry.get("source_session_ids") or [])
            and set(entry.get("source_message_ids") or []) == expected_message_ids
            and set(entry.get("target_message_ids") or []) == target_message_ids
            and set(entry.get("context_message_ids") or []) == context_message_ids
            and bool(entry.get("consent_scope"))
            for entry in entries
        ):
            raise DatasetError(f"lineage missing for sample_id: {sample_id}")


def _validate_selection_manifest(
    *,
    path: Path,
    expected_digest: str,
    expected_version: str,
    rows: list[dict],
    split_manifest: dict,
    review_summary: dict,
) -> dict:
    selection = _read_json(path, "selection-manifest")
    if file_digest(path) != expected_digest:
        raise DatasetError("selection manifest file digest mismatch")
    if selection.get("selection_version") != expected_version:
        raise DatasetError("selection manifest version mismatch")
    if not isinstance(selection.get("selection_rule"), str) or not str(
        selection.get("selection_rule")
    ).strip():
        raise DatasetError("selection manifest rule missing")

    parent_digest = _require_digest(
        selection.get("parent_candidate_manifest_sha256"),
        "parent_candidate_manifest_sha256",
    )
    candidate_manifest_sha256 = split_manifest.get("candidate_manifest_sha256")
    if not candidate_manifest_sha256:
        raise DatasetError("split manifest candidate identity missing")
    if parent_digest != candidate_manifest_sha256:
        raise DatasetError("selection parent candidate manifest mismatch")

    selected_ids = _require_string_list(
        selection.get("selected_sample_ids"), "selection selected_sample_ids"
    )
    selected_ids_sha256 = _require_digest(
        selection.get("selected_sample_ids_sha256"),
        "selected_sample_ids_sha256",
    )
    if selected_ids_sha256 != digest(selected_ids):
        raise DatasetError("selection selected IDs digest mismatch")
    if selection.get("selected_count") != len(selected_ids):
        raise DatasetError("selection selected_count mismatch")

    reviewed_ids = [str(row.get("sample_id") or "") for row in rows]
    if len(reviewed_ids) != len(set(reviewed_ids)):
        raise DatasetError("reviewed rows contain duplicate selected IDs")
    if not set(reviewed_ids).issubset(set(selected_ids)):
        raise DatasetError("reviewed rows contain an unselected ID")
    split_counts = selection.get("split_counts")
    if not isinstance(split_counts, dict) or set(split_counts) != set(_SPLITS):
        raise DatasetError("selection split counts invalid")
    if any(not isinstance(split_counts[split], int) or split_counts[split] <= 0 for split in _SPLITS):
        raise DatasetError("selection split counts invalid")
    if sum(split_counts.values()) != len(selected_ids):
        raise DatasetError("selection split counts mismatch")
    if review_summary.get("selected_count") != len(selected_ids):
        raise DatasetError("review summary selected_count mismatch")
    if review_summary.get("selected_sample_ids_sha256") != selected_ids_sha256:
        raise DatasetError("review summary selected IDs digest mismatch")
    if review_summary.get("selected_counts_by_split") != split_counts:
        raise DatasetError("review summary selected split counts mismatch")
    rejected_by_split = review_summary.get("rejected_counts_by_split")
    if not isinstance(rejected_by_split, dict) or set(rejected_by_split) != set(_SPLITS):
        raise DatasetError("review summary rejected split counts missing")
    if any(
        not isinstance(rejected_by_split[split], int)
        or rejected_by_split[split] < 0
        or rejected_by_split[split] > split_counts[split]
        for split in _SPLITS
    ):
        raise DatasetError("review summary rejected split counts invalid")
    exported_counts = {
        split: len(ids) for split, ids in _sample_ids_by_split(rows).items()
    }
    if exported_counts != {
        split: split_counts[split] - rejected_by_split[split]
        for split in _SPLITS
    }:
        raise DatasetError("reviewed subset split counts do not match review decisions")
    return selection


def validate_formal_inputs(
    *,
    config: dict,
    reviewed_jsonl: Path,
    source_manifest_path: Path,
    split_manifest_path: Path,
    privacy_report_path: Path,
    lineage_path: Path,
    review_summary_path: Path,
    selection_manifest_path: Path | None = None,
) -> tuple[list[dict], dict]:
    """Validate all formal export evidence without writing a snapshot."""

    rows = _read_jsonl(reviewed_jsonl, "reviewed-jsonl")
    source_manifest = _read_json(source_manifest_path, "source-manifest")
    split_manifest = _read_json(split_manifest_path, "split-manifest")
    privacy_report = _read_json(privacy_report_path, "privacy-report")
    review_summary = _read_json(review_summary_path, "review-summary")
    lineage = _read_jsonl(lineage_path, "lineage")

    dataset = config.get("dataset") or {}
    identity = {field: dataset.get(field) for field in _IDENTITY_FIELDS}
    for field in _IDENTITY_FIELDS:
        if not identity.get(field):
            raise DatasetError(f"dataset.{field} is required")
    for field in (
        "source_sha256",
        "manifest_sha256",
        "split_sha256",
        "consent_digest",
        "review_evidence_digest",
    ):
        _require_digest(identity[field], f"dataset.{field}")

    if identity["dataset_id"] != source_manifest.get("dataset_id"):
        raise DatasetError("dataset_id mismatch")
    if identity["source_sha256"] != source_manifest.get("source_sha256"):
        raise DatasetError("source_sha256 mismatch")
    if identity["manifest_sha256"] != source_manifest.get("manifest_sha256"):
        raise DatasetError("source manifest digest mismatch")
    if source_manifest.get("manifest_sha256") != _manifest_digest(source_manifest):
        raise DatasetError("source manifest is not self-consistent")
    if identity["consent_digest"] != source_manifest.get("consent_digest"):
        raise DatasetError("consent_digest mismatch")
    if source_manifest.get("authorization_status") != "verified":
        raise DatasetError("source manifest consent authorization is not verified")
    if not isinstance(source_manifest.get("consent_id"), str) or not str(
        source_manifest.get("consent_id")
    ).strip():
        raise DatasetError("source manifest consent_id missing")
    _require_digest(
        source_manifest.get("consent_file_sha256"),
        "source manifest consent_file_sha256",
    )
    for field in (
        "raw_message_count",
        "filtered_message_count",
        "retained_self_target_text_count",
    ):
        value = source_manifest.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DatasetError(f"source manifest {field} is invalid")
    if source_manifest["filtered_message_count"] > source_manifest["raw_message_count"]:
        raise DatasetError("source manifest filtered count exceeds raw count")
    if (
        source_manifest["retained_self_target_text_count"]
        > source_manifest["filtered_message_count"]
    ):
        raise DatasetError("source manifest retained count exceeds filtered count")
    excluded_by_consent = source_manifest.get("excluded_by_consent")
    if not isinstance(excluded_by_consent, dict) or set(excluded_by_consent) != {
        "message_type",
        "time_scope",
        "third_party",
    }:
        raise DatasetError("source manifest consent exclusion counts are invalid")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in excluded_by_consent.values()
    ):
        raise DatasetError("source manifest consent exclusion counts are invalid")
    if (
        source_manifest["filtered_message_count"]
        + sum(excluded_by_consent.values())
        != source_manifest["raw_message_count"]
    ):
        raise DatasetError("source manifest consent counts do not reconcile")
    for field in (
        "unknown_role_count",
        "duplicate_message_id_count",
        "timestamp_parse_failure_count",
    ):
        _require_exact_zero(source_manifest.get(field), f"source manifest {field}")
    redaction_counts = source_manifest.get("redaction_counts")
    if not isinstance(redaction_counts, dict) or set(redaction_counts) != {
        "cross_message_secret_redactions"
    }:
        raise DatasetError("source manifest redaction counts are invalid")
    cross_message_redactions = redaction_counts["cross_message_secret_redactions"]
    if (
        isinstance(cross_message_redactions, bool)
        or not isinstance(cross_message_redactions, int)
        or cross_message_redactions < 0
    ):
        raise DatasetError("source manifest redaction counts are invalid")
    for field in ("preprocessing_version", "redaction_version", "scanner_version"):
        if identity[field] != source_manifest.get(field):
            raise DatasetError(f"{field} mismatch")
    if identity["split_sha256"] != split_manifest.get("split_sha256"):
        raise DatasetError("split_sha256 mismatch")
    split_mapping = split_manifest.get("session_ids_by_split")
    if not isinstance(split_mapping, dict):
        raise DatasetError("split manifest session mapping missing")
    expected_split_sha256 = digest({
        split: list(split_mapping.get(split) or [])
        for split in ("train", "validation", "test")
    })
    if split_manifest.get("split_sha256") != expected_split_sha256:
        raise DatasetError("split manifest mapping does not match split_sha256")
    split_manifest_sha256 = split_manifest.get("manifest_sha256")
    if not _DIGEST_RE.fullmatch(str(split_manifest_sha256 or "")):
        raise DatasetError("split manifest digest missing")
    calculated_split_manifest_digest = digest({
        key: value for key, value in split_manifest.items()
        if key not in {"split_sha256", "manifest_sha256"}
    })
    if split_manifest_sha256 != calculated_split_manifest_digest:
        raise DatasetError("split manifest is not self-consistent")
    if source_manifest.get("split_sha256") != identity["split_sha256"]:
        raise DatasetError("source/split manifest mismatch")
    candidate_manifest_sha256 = _require_digest(
        split_manifest.get("candidate_manifest_sha256"),
        "split manifest candidate_manifest_sha256",
    )
    if source_manifest.get("candidate_manifest_sha256") != candidate_manifest_sha256:
        raise DatasetError("source/split candidate manifest mismatch")

    report_hard_leak_count = privacy_report.get("hard_leak_count")
    if (
        privacy_report.get("status") != "pass"
        or isinstance(report_hard_leak_count, bool)
        or not isinstance(report_hard_leak_count, int)
        or report_hard_leak_count != 0
    ):
        raise DatasetError("privacy report is not passing")
    if privacy_report.get("scanner_version") != identity["scanner_version"]:
        raise DatasetError("privacy report scanner version mismatch")
    for mapping_name in ("session_counts", "candidate_counts"):
        counts = split_manifest.get(mapping_name)
        if not isinstance(counts, dict) or set(counts) != set(_SPLITS):
            raise DatasetError(f"split manifest {mapping_name} is invalid")
        if any(
            isinstance(counts[split], bool)
            or not isinstance(counts[split], int)
            or counts[split] < 0
            for split in _SPLITS
        ):
            raise DatasetError(f"split manifest {mapping_name} is invalid")
    if split_manifest["session_counts"] != {
        split: len(list(split_mapping.get(split) or [])) for split in _SPLITS
    }:
        raise DatasetError("split manifest session counts do not match session mapping")
    if sum(split_manifest["candidate_counts"].values()) <= 0:
        raise DatasetError("split manifest candidate counts must be positive")
    candidate_count = review_summary.get("candidate_count")
    expected_candidate_count = sum(split_manifest["candidate_counts"].values())
    if (
        isinstance(candidate_count, bool)
        or not isinstance(candidate_count, int)
        or candidate_count <= 0
    ):
        raise DatasetError("review summary candidate_count must be positive")
    if candidate_count != expected_candidate_count:
        raise DatasetError("review candidate count does not match split manifest")
    expected_privacy_counts = {
        "message_count": source_manifest["filtered_message_count"],
        "session_count": sum(split_manifest["session_counts"].values()),
        "candidate_count": sum(split_manifest["candidate_counts"].values()),
    }
    for field, expected in expected_privacy_counts.items():
        value = privacy_report.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value != expected:
            raise DatasetError(f"privacy report {field} mismatch")
    if privacy_report.get("excluded_by_consent") != excluded_by_consent:
        raise DatasetError("privacy report consent exclusion counts mismatch")
    if privacy_report.get("redaction_counts") != redaction_counts:
        raise DatasetError("privacy report redaction counts mismatch")
    for layer in ("messages", "sessions", "candidates"):
        _privacy_zero(privacy_report, layer)
        source_privacy = (source_manifest.get("privacy_counts") or {}).get(layer)
        source_hard_leak_count = (
            source_privacy.get("hard_leak_count")
            if isinstance(source_privacy, dict)
            else None
        )
        if (
            not isinstance(source_privacy, dict)
            or isinstance(source_hard_leak_count, bool)
            or not isinstance(source_hard_leak_count, int)
            or source_hard_leak_count != 0
        ):
            raise DatasetError(f"source manifest privacy evidence blocked: {layer}")
        privacy_layer = (privacy_report.get("layers") or {}).get(layer)
        if privacy_layer != source_privacy:
            raise DatasetError(f"privacy report/source manifest mismatch: {layer}")

    actual_review_digest = file_digest(reviewed_jsonl)
    if review_summary.get("review_evidence_digest") != actual_review_digest:
        raise DatasetError("review summary digest mismatch")
    if identity["review_evidence_digest"] != actual_review_digest:
        raise DatasetError("configured review evidence digest mismatch")
    uncertain_count = review_summary.get("uncertain_count")
    if (
        isinstance(uncertain_count, bool)
        or not isinstance(uncertain_count, int)
        or uncertain_count != 0
    ):
        raise DatasetError("review remains uncertain")
    reviewed_hard_leak_count = review_summary.get("reviewed_hard_leak_count")
    if (
        isinstance(reviewed_hard_leak_count, bool)
        or not isinstance(reviewed_hard_leak_count, int)
        or reviewed_hard_leak_count != 0
    ):
        raise DatasetError("reviewed privacy evidence blocked")
    if review_summary.get("human_review_completed") is not True:
        raise DatasetError("human review is incomplete")
    if review_summary.get("candidate_manifest_sha256") != candidate_manifest_sha256:
        raise DatasetError("review candidate manifest mismatch")
    selected_count = review_summary.get("selected_count")
    event_count = review_summary.get("event_count")
    if not isinstance(selected_count, int) or selected_count <= 0:
        raise DatasetError("review summary selected_count must be positive")
    selected_scope = review_summary.get("selected_scope")
    if selected_scope not in {"all", "subset"}:
        raise DatasetError("review summary selected_scope must be all or subset")
    if not isinstance(event_count, int) or event_count < 0:
        raise DatasetError("review summary event_count must be non-negative")
    if selected_scope == "all":
        if selected_count != candidate_count or event_count != candidate_count:
            raise DatasetError("candidate/event/review scope mismatch")
    elif selected_count > candidate_count or event_count != selected_count:
        raise DatasetError("selected candidate/event/review scope mismatch")
    if len(rows) != review_summary.get("exported_count"):
        raise DatasetError("reviewed export count mismatch")
    status_counts = review_summary.get("status_counts")
    if not isinstance(status_counts, dict):
        raise DatasetError("review summary status_counts missing")
    if any(
        not isinstance(status_counts.get(status), int)
        or status_counts.get(status) < 0
        for status in ("keep", "redact_keep", "reject", "uncertain")
    ):
        raise DatasetError("review summary status_counts invalid")
    if set(status_counts) != {"keep", "redact_keep", "reject", "uncertain"}:
        raise DatasetError("review summary status_counts has unknown fields")
    if sum(status_counts.values()) != event_count:
        raise DatasetError("review summary status_counts do not match event_count")
    if status_counts["uncertain"] != 0:
        raise DatasetError("review remains uncertain")
    if status_counts["keep"] + status_counts["redact_keep"] != len(rows):
        raise DatasetError("reviewed export/status count mismatch")

    selection_digest = review_summary.get("selection_manifest_digest")
    selection_version = review_summary.get("selection_version")
    if bool(selection_digest) != bool(selection_version):
        raise DatasetError("selection version and digest must be provided together")
    if selection_digest:
        _require_digest(selection_digest, "selection_manifest_digest")
        if dataset.get("selection_manifest_digest") != selection_digest:
            raise DatasetError("selection manifest digest mismatch")
        if dataset.get("selection_version") != selection_version:
            raise DatasetError("selection version mismatch")
        if selected_scope != "subset":
            raise DatasetError("selection manifest is only valid for subset review")
        if selection_manifest_path is None:
            raise DatasetError("selection manifest input is required")
        _validate_selection_manifest(
            path=selection_manifest_path,
            expected_digest=selection_digest,
            expected_version=str(selection_version),
            rows=rows,
            split_manifest=split_manifest,
            review_summary=review_summary,
        )
    elif dataset.get("selection_manifest_digest") or dataset.get("selection_version"):
        raise DatasetError("unexpected configured selection identity")
    elif selected_scope == "subset" or selection_manifest_path is not None:
        raise DatasetError("subset review requires a bound selection manifest")

    if selection_manifest_path is not None:
        selection_identity = {
            "selection_version": selection_version,
            "selection_manifest_digest": selection_digest,
        }
        identity.update(selection_identity)

    split_data = build_sft_splits(
        rows, require_nonempty=True, require_human_evidence=True
    )
    frozen_split_by_session = {
        str(session_id): split
        for split in _SPLITS
        for session_id in list(split_mapping.get(split) or [])
    }
    for row in rows:
        session_id = str(row.get("session_id") or "")
        split = str((row.get("metadata") or {}).get("split") or "")
        if frozen_split_by_session.get(session_id) != split:
            raise DatasetError("reviewed row does not match the frozen session split")
    expected_counts = split_manifest.get("candidate_counts")
    if not isinstance(expected_counts, dict):
        raise DatasetError("split manifest candidate counts missing")
    if selected_scope == "all":
        rejected_by_split = review_summary.get("rejected_counts_by_split")
        if not isinstance(rejected_by_split, dict) or any(
            not isinstance(rejected_by_split.get(split), int)
            or rejected_by_split.get(split) < 0
            for split in _SPLITS
        ):
            raise DatasetError("review summary rejected split counts missing")
        if any(
            not isinstance(expected_counts.get(split), int)
            or expected_counts.get(split) < 0
            for split in _SPLITS
        ):
            raise DatasetError("split manifest candidate counts invalid")
        expected_export_counts = {
            split: expected_counts[split] - rejected_by_split[split]
            for split in _SPLITS
        }
        if any(value < 0 for value in expected_export_counts.values()):
            raise DatasetError("review rejected split count exceeds candidates")
        if split_data["manifest"]["sample_counts"] != expected_export_counts:
            raise DatasetError("reviewed split counts do not match review decisions")
    _validate_lineage(rows, lineage)
    return rows, identity


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only dataset plan/check boundary")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--check-approved", action="store_true")
    parser.add_argument("--execute", action="store_true", help="create a new formal snapshot after all gates")
    parser.add_argument("--reviewed-jsonl", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--split-manifest", type=Path)
    parser.add_argument("--privacy-report", type=Path)
    parser.add_argument("--lineage", type=Path)
    parser.add_argument("--review-summary", type=Path)
    parser.add_argument("--selection-manifest", type=Path)
    args = parser.parse_args()
    config = load_project_config(args.config)
    errors = validate_project_config(config)
    if args.execute:
        required = {
            "--reviewed-jsonl": args.reviewed_jsonl,
            "--output": args.output,
            "--source-manifest": args.source_manifest,
            "--split-manifest": args.split_manifest,
            "--privacy-report": args.privacy_report,
            "--lineage": args.lineage,
            "--review-summary": args.review_summary,
        }
        if not args.check_approved:
            errors.append("--execute requires --check-approved")
        errors.extend(f"--execute requires {name}" for name, value in required.items() if value is None)
        if not errors:
            try:
                rows, identity = validate_formal_inputs(
                    config=config,
                    reviewed_jsonl=args.reviewed_jsonl,
                    source_manifest_path=args.source_manifest,
                    split_manifest_path=args.split_manifest,
                    privacy_report_path=args.privacy_report,
                    lineage_path=args.lineage,
                    review_summary_path=args.review_summary,
                    selection_manifest_path=args.selection_manifest,
                )
                manifest = write_sft_snapshot(args.output, rows, identity=identity)
            except (DatasetError, FileExistsError, OSError, ValueError) as exc:
                errors.append(str(exc))
            else:
                print(json.dumps({"status": "completed", "manifest": manifest, "will_write_formal_data": True, "will_create_mlflow_run": False}, ensure_ascii=False, sort_keys=True))
                return 0
    elif args.check_approved:
        errors.append("formal dataset export requires explicit --execute after verified consent and completed human review")
    print(json.dumps({"status": "planned" if not errors else "blocked", "errors": errors, "will_write_formal_data": False, "will_create_mlflow_run": False}, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 2

if __name__ == "__main__":
    raise SystemExit(main())
