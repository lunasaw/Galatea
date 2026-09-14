"""Compile GPT prelabels into an immutable experimental train/validation view."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any, Iterator, Mapping

from ._common import digest, file_digest
from .canary import CANARY_SCANNER_VERSION, DEFAULT_PATTERN, _contents
from .curation import CONTROLLED_ROOT, CURATION_SCHEMA_VERSION
from .datasets import _publish_directory_noreplace
from .redact import scan_candidate
from .review import _content_hash


PRELABEL_REVIEWER_ID = "gpt-prelabel-v1"
PRELABEL_SNAPSHOT_SCHEMA = "wechat-persona-gpt-prelabel-snapshot-v1"
PRELABEL_SELECTION_VERSION = "gpt-prelabel-v1"
SPLITS = ("train", "validation")


class PrelabelSnapshotError(ValueError):
    pass


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrelabelSnapshotError(f"cannot read {label}") from exc
    if not isinstance(value, dict):
        raise PrelabelSnapshotError(f"{label} must contain an object")
    return value


def _regular_below(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    controlled = root.resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise PrelabelSnapshotError(f"{label} must be a regular non-symlink file")
    if resolved == controlled or not resolved.is_relative_to(controlled):
        raise PrelabelSnapshotError(f"{label} must be below {controlled}")
    return resolved


def _directory_below(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    controlled = root.resolve()
    if resolved == controlled or not resolved.is_relative_to(controlled):
        raise PrelabelSnapshotError(f"{label} must be below {controlled}")
    if resolved.exists() and (not resolved.is_dir() or resolved.is_symlink()):
        raise PrelabelSnapshotError(f"{label} must be a non-symlink directory")
    return resolved


def _iter_jsonl(path: Path, split: str) -> Iterator[tuple[dict[str, Any], bytes]]:
    with path.open("rb") as handle:
        for line_number, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PrelabelSnapshotError(
                    f"invalid JSON in {split} row {line_number}"
                ) from exc
            if not isinstance(value, dict):
                raise PrelabelSnapshotError(
                    f"{split} row {line_number} must contain an object"
                )
            yield value, raw


def _index_decisions(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    decisions = state.get("decisions")
    if not isinstance(decisions, list):
        raise PrelabelSnapshotError("review state decisions must be an array")
    indexed: dict[str, dict[str, Any]] = {}
    for value in decisions:
        if not isinstance(value, dict):
            raise PrelabelSnapshotError("review decision must contain an object")
        sample_id = str(value.get("sample_id") or "")
        if not sample_id or sample_id in indexed:
            raise PrelabelSnapshotError("review decisions contain missing or duplicate sample IDs")
        if value.get("reviewer_id") != PRELABEL_REVIEWER_ID:
            raise PrelabelSnapshotError("all effective decisions must be GPT prelabels")
        if value.get("review_status") not in {"keep", "reject"}:
            raise PrelabelSnapshotError("GPT prelabels must resolve to keep or reject")
        if not value.get("reviewed_at") or int(value.get("revision", 0)) < 1:
            raise PrelabelSnapshotError("GPT prelabel evidence is incomplete")
        indexed[sample_id] = value
    return indexed


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def compile_prelabel_snapshot(
    *,
    curated_dataset: Path,
    review_state_path: Path,
    output_root: Path,
    authorization_id: str,
    execute: bool = False,
    controlled_root: Path = CONTROLLED_ROOT,
) -> dict[str, Any]:
    """Validate and optionally publish one machine-reviewed experimental view."""

    controlled_root = controlled_root.resolve()
    curated_dataset = _directory_below(
        curated_dataset, controlled_root, "curated dataset"
    )
    review_state_path = _regular_below(
        review_state_path, controlled_root, "review state"
    )
    output_root = _directory_below(output_root, controlled_root, "output root")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", authorization_id):
        raise PrelabelSnapshotError("authorization_id has an invalid format")

    curated_manifest_path = _regular_below(
        curated_dataset / "selection-manifest.json",
        controlled_root,
        "curated manifest",
    )
    curated_manifest = _read_json(curated_manifest_path, "curated manifest")
    review_state = _read_json(review_state_path, "review state")
    if curated_manifest.get("schema_version") != CURATION_SCHEMA_VERSION:
        raise PrelabelSnapshotError("unsupported curated dataset schema")
    if review_state.get("schema_version") != "wechat-persona-review-workspace-v1":
        raise PrelabelSnapshotError("unsupported review workspace schema")
    for key in ("curation_id", "curation_digest", "selection_sha256"):
        if review_state.get(key) != curated_manifest.get(key):
            raise PrelabelSnapshotError(f"review state {key} mismatch")
    if review_state.get("manifest_file_sha256") != file_digest(curated_manifest_path):
        raise PrelabelSnapshotError("review state manifest digest mismatch")
    if review_state.get("training_eligible") is not False:
        raise PrelabelSnapshotError("review state must remain training-ineligible")
    if review_state.get("formal_approval_required") is not True:
        raise PrelabelSnapshotError("review state must require formal approval")

    decisions = _index_decisions(review_state)
    expected_count = sum(int(curated_manifest["selected_counts"][split]) for split in SPLITS)
    if len(decisions) != expected_count:
        raise PrelabelSnapshotError("review decisions do not cover the curated dataset")

    parent_manifest_path = _regular_below(
        Path(str(curated_manifest["parent"]["manifest_path"])),
        controlled_root,
        "parent snapshot manifest",
    )
    if file_digest(parent_manifest_path) != curated_manifest["parent"]["manifest_file_sha256"]:
        raise PrelabelSnapshotError("parent snapshot manifest digest mismatch")
    parent_manifest = _read_json(parent_manifest_path, "parent snapshot manifest")
    if parent_manifest.get("formal_training_eligible") is not False:
        raise PrelabelSnapshotError("parent snapshot must retain approval-separated state")

    rows_by_split: dict[str, list[tuple[dict[str, Any], bytes]]] = {}
    selected_ids: dict[str, list[str]] = {split: [] for split in SPLITS}
    selected_sessions: dict[str, set[str]] = {split: set() for split in SPLITS}
    selected_label_counts: dict[str, int] = {}
    observed_ids: set[str] = set()
    canary_matches = 0
    for split in SPLITS:
        split_path = _regular_below(
            curated_dataset / f"{split}.jsonl", controlled_root, f"curated {split}"
        )
        rows = list(_iter_jsonl(split_path, split))
        if len(rows) != int(curated_manifest["selected_counts"][split]):
            raise PrelabelSnapshotError(f"curated {split} count mismatch")
        rows_by_split[split] = rows
        for row, _raw in rows:
            sample_id = str(row.get("sample_id") or "")
            session_id = str(row.get("session_id") or "")
            if not sample_id or not session_id or sample_id in observed_ids:
                raise PrelabelSnapshotError("curated rows contain invalid identity")
            observed_ids.add(sample_id)
            event = decisions.get(sample_id)
            if event is None:
                raise PrelabelSnapshotError("review decision is missing for a curated row")
            if str(event.get("session_id") or "") != session_id:
                raise PrelabelSnapshotError("review decision session mismatch")
            if str(event.get("content_sha256") or "") != _content_hash(row):
                raise PrelabelSnapshotError("review decision content digest mismatch")
            if scan_candidate(row).get("hard_leak_count") != 0:
                raise PrelabelSnapshotError("selected source failed privacy rescan")
            if event["review_status"] == "keep":
                selected_ids[split].append(sample_id)
                selected_sessions[split].add(
                    hashlib.sha256(session_id.encode("utf-8")).hexdigest()
                )
                for label in event.get("labels") or []:
                    selected_label_counts[str(label)] = selected_label_counts.get(str(label), 0) + 1
                canary_matches += sum(
                    bool(DEFAULT_PATTERN.search(value)) for value in _contents(row)
                )
    if observed_ids != set(decisions):
        raise PrelabelSnapshotError("review state contains decisions outside the curated dataset")
    if not all(selected_ids.values()):
        raise PrelabelSnapshotError("GPT selection must keep non-empty train and validation splits")
    if selected_sessions["train"] & selected_sessions["validation"]:
        raise PrelabelSnapshotError("selected session crosses train and validation")
    if canary_matches:
        raise PrelabelSnapshotError("selected rows failed canary scan")

    decision_digest = file_digest(review_state_path)
    selection_digest = digest(
        {
            "schema_version": PRELABEL_SNAPSHOT_SCHEMA,
            "curation_digest": curated_manifest["curation_digest"],
            "review_state_sha256": decision_digest,
            "selected_sample_ids": selected_ids,
        }
    )
    dataset_id = f"wechat-gpt-prelabel-v1-{selection_digest[:20]}"
    output_directory = output_root / dataset_id
    parent_test = curated_manifest["parent"]["splits"]["test"]
    split_sha256 = digest(
        {
            "train": selected_ids["train"],
            "validation": selected_ids["validation"],
            "test_identity": parent_test["sha256"],
        }
    )
    plan = {
        "status": "planned" if not execute else "published",
        "dataset_id": dataset_id,
        "output_directory": str(output_directory),
        "selected_counts": {split: len(selected_ids[split]) for split in SPLITS},
        "rejected_count": len(decisions) - sum(len(value) for value in selected_ids.values()),
        "selection_sha256": selection_digest,
        "split_sha256": split_sha256,
        "review_state_sha256": decision_digest,
        "reviewer_id": PRELABEL_REVIEWER_ID,
        "human_review_completed": False,
        "experimental_training_authorized": True,
        "formal_training_eligible": False,
        "test_access": "untouched",
        "will_write": execute,
    }
    if not execute:
        return plan
    if output_directory.exists():
        raise FileExistsError(f"prelabel snapshot already exists: {output_directory}")
    output_root.mkdir(parents=True, exist_ok=True, mode=stat.S_IRWXU)
    output_root.chmod(stat.S_IRWXU)
    staging = Path(tempfile.mkdtemp(prefix=f".{dataset_id}.staging-", dir=output_root))
    staging.chmod(stat.S_IRWXU)
    try:
        split_hashes: dict[str, str] = {}
        for split in SPLITS:
            kept = set(selected_ids[split])
            destination = staging / f"{split}.jsonl"
            with destination.open("xb") as output:
                for row, raw in rows_by_split[split]:
                    if str(row["sample_id"]) in kept:
                        output.write(raw if raw.endswith(b"\n") else raw + b"\n")
            destination.chmod(stat.S_IRUSR | stat.S_IWUSR)
            split_hashes[split] = file_digest(destination)

        canary_evidence = {
            "schema_version": "wechat-canary-evidence-v1",
            "scanner_version": CANARY_SCANNER_VERSION,
            "pattern_sha256": hashlib.sha256(
                DEFAULT_PATTERN.pattern.encode("utf-8")
            ).hexdigest(),
            "scope": ["train", "validation"],
            "split_file_sha256": split_hashes,
            "sample_counts": {split: len(selected_ids[split]) for split in SPLITS},
            "scanned_sample_count": sum(len(value) for value in selected_ids.values()),
            "match_count": 0,
            "status": "pass",
            "test_access": "untouched",
        }
        canary_path = staging / "canary-evidence.json"
        _write_json(canary_path, canary_evidence)
        canary_report_sha256 = file_digest(canary_path)
        preprocessing = (
            str(parent_manifest["preprocessing_version"])
            + "+girlfriend-assistant-usefulness-v1+gpt-prelabel-v1"
        )
        manifest = {
            "schema_version": PRELABEL_SNAPSHOT_SCHEMA,
            "dataset_id": dataset_id,
            "source_sha256": parent_manifest["source_sha256"],
            "consent_digest": parent_manifest["consent_digest"],
            "preprocessing_version": preprocessing,
            "split_sha256": split_sha256,
            "split_file_sha256": {
                **split_hashes,
                "test": parent_test["sha256"],
            },
            "sample_counts": {
                **{split: len(selected_ids[split]) for split in SPLITS},
                "test": int(parent_test["count"]),
            },
            "selection": {
                "version": PRELABEL_SELECTION_VERSION,
                "selection_sha256": selection_digest,
                "reviewer_id": PRELABEL_REVIEWER_ID,
                "review_state_sha256": decision_digest,
                "decision_count": len(decisions),
                "kept_count": sum(len(value) for value in selected_ids.values()),
                "rejected_count": plan["rejected_count"],
                "label_counts": dict(sorted(selected_label_counts.items())),
            },
            "parent": {
                "curation_id": curated_manifest["curation_id"],
                "curation_digest": curated_manifest["curation_digest"],
                "manifest_file_sha256": file_digest(curated_manifest_path),
                "formal_dataset_id": parent_manifest["dataset_id"],
                "formal_manifest_sha256": parent_manifest["manifest_sha256"],
                "formal_review_evidence_digest": parent_manifest["review_evidence_digest"],
                "human_review_completed": True,
                "test_identity": parent_test["sha256"],
                "test_untouched": True,
            },
            "privacy": {
                "pii_scan_passed": True,
                "hard_leak_count": 0,
                "canary_scan_passed": True,
                "canary_report_sha256": canary_report_sha256,
                "canary_scanned_sample_count": canary_evidence["scanned_sample_count"],
            },
            "governance": {
                "authorization_id": authorization_id,
                "quality_status": "experimental_only",
                "machine_review_completed": True,
                "human_review_completed": False,
                "experimental_training_authorized": True,
                "formal_training_eligible": False,
                "promotable": False,
                "test_access": "untouched",
            },
        }
        manifest["manifest_sha256"] = digest(manifest)
        _write_json(staging / "manifest.json", manifest)
        _publish_directory_noreplace(staging, output_directory)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        **plan,
        "manifest_path": str(output_directory / "manifest.json"),
        "manifest_sha256": manifest["manifest_sha256"],
        "canary_report_sha256": canary_report_sha256,
        "split_file_sha256": manifest["split_file_sha256"],
        "sample_counts": manifest["sample_counts"],
        "preprocessing_version": manifest["preprocessing_version"],
    }


__all__ = [
    "PRELABEL_REVIEWER_ID",
    "PRELABEL_SNAPSHOT_SCHEMA",
    "PrelabelSnapshotError",
    "compile_prelabel_snapshot",
]
