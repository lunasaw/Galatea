"""Loopback-only review service for one immutable daily-memory snapshot."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from ._common import canonical_json, digest, file_digest
from .curation import CONTROLLED_ROOT


class FactReviewServerError(ValueError):
    """Raised when a fact-review request or workspace violates its contract."""


REVIEW_STATUSES = frozenset({"confirmed", "deferred", "rejected"})
FILTER_STATUSES = frozenset({"all", "pending", *REVIEW_STATUSES})
CONFLICT_STATUSES = frozenset({"all", "none", "conflicted", "insufficient"})
RISK_FILTERS = frozenset({"all", "eligible", "blocked", "unresolved"})
REASON_CODES = frozenset(
    {
        "evidence_confirmed",
        "conflict_unresolved",
        "needs_more_context",
        "temporal_unresolved",
        "not_owner_fact",
        "quoted_or_hypothetical",
        "negative_or_uncertain",
        "duplicate_or_malformed",
        "not_durable_memory",
        "other",
    }
)
MAX_REQUEST_BYTES = 64 * 1024
DEFAULT_PAGE_SIZE = 40
MAX_PAGE_SIZE = 100
MAX_SEARCH_CHARS = 200
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")


def _below(path: Path, root: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    controlled = root.expanduser().resolve()
    if resolved == controlled or controlled not in resolved.parents:
        raise FactReviewServerError(f"{label} must be below {controlled}")
    return resolved


def _regular(path: Path, label: str) -> Path:
    if not path.is_file() or path.is_symlink():
        raise FactReviewServerError(f"{label} must be a regular non-symlink file")
    return path


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FactReviewServerError(f"{label} must be readable UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise FactReviewServerError(f"{label} must contain an object")
    return value


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise FactReviewServerError(
                        f"invalid {label} JSONL at line {line_number}"
                    ) from exc
                if not isinstance(value, dict):
                    raise FactReviewServerError(
                        f"invalid {label} object at line {line_number}"
                    )
                rows.append(value)
    except (OSError, UnicodeDecodeError) as exc:
        raise FactReviewServerError(f"{label} must be readable UTF-8 JSONL") from exc
    return rows


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=stat.S_IRWXU)
    path.parent.chmod(stat.S_IRWXU)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary_name).chmod(stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _append_audit(path: Path, event: Mapping[str, Any]) -> None:
    _append_audit_many(path, [event])


def _append_audit_many(path: Path, events: list[Mapping[str, Any]]) -> None:
    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=stat.S_IRWXU)
    path.parent.chmod(stat.S_IRWXU)
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(
                json.dumps(
                    event,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _candidate_digest(candidate: Mapping[str, Any]) -> str:
    return digest(dict(candidate))


def _candidate_eligible(extractions: list[Mapping[str, Any]]) -> bool:
    return bool(extractions) and any(
        row.get("polarity") == "positive"
        and row.get("claim_type") in {"explicit", "relative"}
        for row in extractions
    ) and all(not row.get("unresolved_references") for row in extractions)


def _candidate_risks(extractions: list[Mapping[str, Any]]) -> list[str]:
    risks: set[str] = set()
    if any(row.get("unresolved_references") for row in extractions):
        risks.add("unresolved_reference")
    if not any(row.get("polarity") == "positive" for row in extractions):
        risks.add("no_positive_evidence")
    if not any(
        row.get("claim_type") in {"explicit", "relative"} for row in extractions
    ):
        risks.add("non_direct_claim")
    if any(row.get("polarity") in {"negative", "uncertain"} for row in extractions):
        risks.add("mixed_or_non_positive_polarity")
    if any(row.get("claim_type") in {"quoted", "hypothetical"} for row in extractions):
        risks.add("quoted_or_hypothetical")
    if any(row.get("date_basis") == "unknown" for row in extractions):
        risks.add("unknown_date_basis")
    return sorted(risks)


class FactReviewStore:
    """Validated snapshot reader and append-only fact-review workspace."""

    def __init__(
        self,
        *,
        snapshot_dir: Path,
        review_dir: Path,
        controlled_root: Path = CONTROLLED_ROOT,
        create_workspace: bool = True,
    ) -> None:
        self.snapshot_dir = _below(snapshot_dir, controlled_root, "snapshot directory")
        self.review_dir = _below(review_dir, controlled_root, "review directory")
        if not self.snapshot_dir.is_dir() or self.snapshot_dir.is_symlink():
            raise FactReviewServerError(
                "snapshot directory must be a non-symlink directory"
            )
        if self.review_dir == self.snapshot_dir or self.snapshot_dir in self.review_dir.parents:
            raise FactReviewServerError(
                "review directory must not be inside the immutable snapshot"
            )
        if self.review_dir.exists() and (
            not self.review_dir.is_dir() or self.review_dir.is_symlink()
        ):
            raise FactReviewServerError(
                "review directory must be a non-symlink directory"
            )
        if create_workspace:
            self.review_dir.mkdir(parents=True, exist_ok=True, mode=stat.S_IRWXU)
            self.review_dir.chmod(stat.S_IRWXU)

        self.manifest_path = _regular(
            self.snapshot_dir / "manifest.json", "daily-memory manifest"
        )
        self.ledger_path = _regular(
            self.snapshot_dir / "fact-ledger.jsonl", "fact ledger"
        )
        self.candidates_path = _regular(
            self.snapshot_dir / "review" / "candidates.jsonl",
            "fact candidates",
        )
        self.manifest = _read_json(self.manifest_path, "daily-memory manifest")
        self.manifest_file_sha256 = file_digest(self.manifest_path)
        self._validate_manifest()

        self.extractions = self._load_extractions()
        self.groups = self._load_groups()
        self.by_id = {str(group["fact_group_id"]): group for group in self.groups}
        if len(self.by_id) != len(self.groups):
            raise FactReviewServerError("fact ledger contains duplicate group IDs")
        self._details = {
            group_id: self._build_group_detail(group)
            for group_id, group in self.by_id.items()
        }

        self._latest_path = self.review_dir / "review-state.json"
        self._audit_path = self.review_dir / "review-events.audit.jsonl"
        self._lock = threading.Lock()
        self.latest = self._load_existing()

    @property
    def snapshot_id(self) -> str:
        return str(self.manifest["snapshot_id"])

    @property
    def manifest_sha256(self) -> str:
        return str(self.manifest["manifest_sha256"])

    @property
    def ledger_sha256(self) -> str:
        return str(self.manifest["output_digests"]["fact_ledger"])

    @property
    def candidates_sha256(self) -> str:
        return str(self.manifest["output_digests"]["review_candidates"])

    def _validate_manifest(self) -> None:
        manifest = self.manifest
        if manifest.get("schema_version") != "daily-memory-snapshot-v1":
            raise FactReviewServerError("unsupported daily-memory snapshot schema")
        expected_manifest_digest = digest(
            {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        )
        if manifest.get("manifest_sha256") != expected_manifest_digest:
            raise FactReviewServerError("daily-memory manifest digest mismatch")
        if (
            manifest.get("status") != "candidate_review_pending"
            or manifest.get("authorization_status") != "verified"
            or manifest.get("human_review_completed") is not False
            or manifest.get("formal_training_eligible") is not False
            or manifest.get("training_run") is not False
            or manifest.get("creates_mlflow_run") is not False
            or manifest.get("confirmed_index_built") is not False
            or manifest.get("rag_index_built") is not False
        ):
            raise FactReviewServerError("daily-memory governance boundary is invalid")
        output_digests = manifest.get("output_digests")
        if not isinstance(output_digests, dict):
            raise FactReviewServerError("daily-memory output digests are missing")
        expected_files = {
            "fact_ledger": self.ledger_path,
            "review_candidates": self.candidates_path,
        }
        for key, path in expected_files.items():
            declared = output_digests.get(key)
            if not isinstance(declared, str) or not _DIGEST_RE.fullmatch(declared):
                raise FactReviewServerError(f"invalid {key} digest")
            if file_digest(path) != declared:
                raise FactReviewServerError(f"{key} digest mismatch")

    def _load_extractions(self) -> dict[str, dict[str, Any]]:
        rows = _read_jsonl(self.candidates_path, "fact candidates")
        expected_count = (self.manifest.get("counts") or {}).get("candidate_count")
        if expected_count != len(rows):
            raise FactReviewServerError("fact candidate count does not match manifest")
        by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            extraction_id = row.get("extraction_id")
            if (
                row.get("schema_version") != "fact-extraction-v1"
                or row.get("status") != "candidate"
                or not isinstance(extraction_id, str)
                or not extraction_id
                or not isinstance(row.get("evidence"), list)
                or not row["evidence"]
            ):
                raise FactReviewServerError("invalid fact candidate contract")
            if extraction_id in by_id:
                raise FactReviewServerError("duplicate extraction ID")
            by_id[extraction_id] = row
        return by_id

    def _load_groups(self) -> list[dict[str, Any]]:
        rows = _read_jsonl(self.ledger_path, "fact ledger")
        expected_count = (self.manifest.get("counts") or {}).get("fact_group_count")
        if expected_count != len(rows):
            raise FactReviewServerError("fact group count does not match manifest")
        for row in rows:
            if (
                row.get("schema_version") != "fact-ledger-v1"
                or row.get("review_status") != "pending"
                or row.get("confirmed_value") is not None
                or row.get("conflict_status") not in CONFLICT_STATUSES - {"all"}
                or not isinstance(row.get("fact_group_id"), str)
                or not isinstance(row.get("fact_key"), str)
                or not isinstance(row.get("candidates"), list)
                or not row["candidates"]
                or not _DIGEST_RE.fullmatch(str(row.get("lineage_digest") or ""))
            ):
                raise FactReviewServerError("invalid fact ledger contract")
        return rows

    def _build_group_detail(self, group: Mapping[str, Any]) -> dict[str, Any]:
        detailed_candidates: list[dict[str, Any]] = []
        fact_key = str(group["fact_key"])
        for candidate in group["candidates"]:
            if not isinstance(candidate, dict):
                raise FactReviewServerError("invalid ledger candidate")
            extraction_ids = candidate.get("extraction_ids")
            if not isinstance(extraction_ids, list) or not extraction_ids:
                raise FactReviewServerError("ledger candidate is missing extraction IDs")
            extractions: list[dict[str, Any]] = []
            for extraction_id in extraction_ids:
                extraction = self.extractions.get(str(extraction_id))
                if extraction is None:
                    raise FactReviewServerError(
                        "ledger candidate references an unknown extraction"
                    )
                if (
                    extraction.get("fact_key") != fact_key
                    or canonical_json(extraction.get("normalized_value"))
                    != canonical_json(candidate.get("normalized_value"))
                ):
                    raise FactReviewServerError(
                        "ledger candidate does not match its extraction"
                    )
                extractions.append(extraction)
            evidence_refs = {
                str(item.get("evidence_ref") or "")
                for extraction in extractions
                for item in extraction.get("evidence") or []
            }
            if evidence_refs != set(candidate.get("evidence_refs") or []):
                raise FactReviewServerError("ledger evidence references do not match")
            detailed_candidates.append(
                {
                    **dict(candidate),
                    "candidate_sha256": _candidate_digest(candidate),
                    "confirmation_eligible": _candidate_eligible(extractions),
                    "risk_flags": _candidate_risks(extractions),
                    "extractions": [
                        {
                            "extraction_id": row["extraction_id"],
                            "day_id": row["day_id"],
                            "session_id": row["session_id"],
                            "chunk_ordinal": row["chunk_ordinal"],
                            "value": row["value"],
                            "normalized_value": row["normalized_value"],
                            "value_type": row["value_type"],
                            "date_precision": row["date_precision"],
                            "date_basis": row["date_basis"],
                            "claim_type": row["claim_type"],
                            "polarity": row["polarity"],
                            "confidence": row["confidence"],
                            "unresolved_references": row["unresolved_references"],
                            "evidence": row["evidence"],
                            "lineage_digest": row["lineage_digest"],
                        }
                        for row in extractions
                    ],
                }
            )
        return {
            "schema_version": "wechat-persona-fact-review-group-v1",
            "fact_group_id": group["fact_group_id"],
            "fact_key": fact_key,
            "aliases": list(group.get("aliases") or []),
            "conflict_status": group["conflict_status"],
            "lineage_digest": group["lineage_digest"],
            "candidates": detailed_candidates,
        }

    def _load_existing(self) -> dict[str, dict[str, Any]]:
        if not self._latest_path.exists():
            return {}
        state = _read_json(self._latest_path, "fact review state")
        if (
            state.get("schema_version")
            != "wechat-persona-fact-review-workspace-v1"
            or state.get("snapshot_id") != self.snapshot_id
            or state.get("manifest_sha256") != self.manifest_sha256
            or state.get("manifest_file_sha256") != self.manifest_file_sha256
            or state.get("fact_ledger_sha256") != self.ledger_sha256
            or state.get("review_candidates_sha256") != self.candidates_sha256
            or state.get("training_eligible") is not False
            or state.get("confirmed_cards_built") is not False
            or state.get("rag_index_built") is not False
        ):
            raise FactReviewServerError(
                "existing fact review state belongs to another snapshot"
            )
        decisions = state.get("decisions")
        if not isinstance(decisions, list):
            raise FactReviewServerError("existing fact review decisions are invalid")
        latest: dict[str, dict[str, Any]] = {}
        for event in decisions:
            if not isinstance(event, dict):
                raise FactReviewServerError("existing fact review event is invalid")
            group_id = str(event.get("fact_group_id") or "")
            group = self.by_id.get(group_id)
            if group is None or event.get("lineage_digest") != group["lineage_digest"]:
                raise FactReviewServerError(
                    "existing fact review decision has an unknown group"
                )
            self._validate_persisted_event(event)
            latest[group_id] = event
        return latest

    def _validate_persisted_event(self, event: Mapping[str, Any]) -> None:
        status = event.get("review_status")
        if status not in REVIEW_STATUSES:
            raise FactReviewServerError("existing fact review status is invalid")
        if event.get("reason_code") not in REASON_CODES:
            raise FactReviewServerError("existing fact review reason is invalid")
        if not event.get("reviewer_id") or not event.get("reviewed_at"):
            raise FactReviewServerError("existing fact review identity is incomplete")
        if not isinstance(event.get("revision"), int) or event["revision"] < 1:
            raise FactReviewServerError("existing fact review revision is invalid")
        selected = event.get("selected_candidate_sha256")
        if status == "confirmed":
            detail = self._details[str(event["fact_group_id"])]
            candidates = {
                row["candidate_sha256"]: row for row in detail["candidates"]
            }
            if selected not in candidates or not candidates[selected][
                "confirmation_eligible"
            ]:
                raise FactReviewServerError(
                    "existing confirmed decision selects an ineligible candidate"
                )
        elif selected is not None:
            raise FactReviewServerError(
                "non-confirmed decision must not select a candidate"
            )

    def _status_counts(self) -> dict[str, int]:
        counts = Counter(
            str(event["review_status"]) for event in self.latest.values()
        )
        return {
            "pending": len(self.groups) - len(self.latest),
            **{status: counts.get(status, 0) for status in sorted(REVIEW_STATUSES)},
        }

    def bootstrap(self) -> dict[str, Any]:
        conflict_counts = Counter(str(row["conflict_status"]) for row in self.groups)
        eligible_groups = sum(
            any(candidate["confirmation_eligible"] for candidate in detail["candidates"])
            for detail in self._details.values()
        )
        return {
            "schema_version": "wechat-persona-fact-review-service-v1",
            "snapshot": {
                "snapshot_id": self.snapshot_id,
                "dataset_id": self.manifest["dataset_id"],
                "manifest_sha256": self.manifest_sha256,
                "manifest_file_sha256": self.manifest_file_sha256,
                "fact_group_count": len(self.groups),
                "candidate_count": len(self.extractions),
                "conflict_counts": {
                    key: conflict_counts.get(key, 0)
                    for key in ("none", "conflicted", "insufficient")
                },
                "eligible_group_count": eligible_groups,
            },
            "review": {
                "decision_count": len(self.latest),
                "status_counts": self._status_counts(),
                "training_eligible": False,
                "confirmed_cards_built": False,
                "rag_index_built": False,
                "formal_approval_required": True,
            },
        }

    def _summary(self, group_id: str) -> dict[str, Any]:
        detail = self._details[group_id]
        event = self.latest.get(group_id)
        candidate_values = [row["normalized_value"] for row in detail["candidates"]]
        source_days = sorted(
            {
                day
                for candidate in detail["candidates"]
                for day in candidate.get("source_days") or []
            }
        )
        support_count = sum(
            int(candidate.get("support_count") or 0)
            for candidate in detail["candidates"]
        )
        return {
            "fact_group_id": group_id,
            "fact_key": detail["fact_key"],
            "aliases": detail["aliases"],
            "conflict_status": detail["conflict_status"],
            "candidate_count": len(detail["candidates"]),
            "candidate_values": candidate_values,
            "support_count": support_count,
            "source_day_min": source_days[0] if source_days else None,
            "source_day_max": source_days[-1] if source_days else None,
            "confirmation_eligible": any(
                candidate["confirmation_eligible"]
                for candidate in detail["candidates"]
            ),
            "has_unresolved": any(
                "unresolved_reference" in candidate["risk_flags"]
                for candidate in detail["candidates"]
            ),
            "review_status": event["review_status"] if event else "pending",
            "revision": event["revision"] if event else 0,
        }

    def page(
        self,
        *,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
        status: str = "pending",
        conflict: str = "all",
        risk: str = "all",
        search: str = "",
    ) -> dict[str, Any]:
        if offset < 0:
            raise FactReviewServerError("offset must be non-negative")
        if limit < 1 or limit > MAX_PAGE_SIZE:
            raise FactReviewServerError(f"limit must be in [1, {MAX_PAGE_SIZE}]")
        if status not in FILTER_STATUSES:
            raise FactReviewServerError("invalid review status filter")
        if conflict not in CONFLICT_STATUSES:
            raise FactReviewServerError("invalid conflict filter")
        if risk not in RISK_FILTERS:
            raise FactReviewServerError("invalid risk filter")
        search = str(search or "").strip()
        if len(search) > MAX_SEARCH_CHARS:
            raise FactReviewServerError("search query is too long")
        needle = search.casefold()
        matched: list[dict[str, Any]] = []
        for group in self.groups:
            group_id = str(group["fact_group_id"])
            summary = self._summary(group_id)
            if status != "all" and summary["review_status"] != status:
                continue
            if conflict != "all" and summary["conflict_status"] != conflict:
                continue
            if risk == "eligible" and not summary["confirmation_eligible"]:
                continue
            if risk == "blocked" and summary["confirmation_eligible"]:
                continue
            if risk == "unresolved" and not summary["has_unresolved"]:
                continue
            if needle:
                searchable = canonical_json(
                    {
                        "fact_key": summary["fact_key"],
                        "aliases": summary["aliases"],
                        "candidate_values": summary["candidate_values"],
                        "fact_group_id": group_id,
                    }
                ).casefold()
                if needle not in searchable:
                    continue
            matched.append(summary)
        priority = {"conflicted": 0, "insufficient": 1, "none": 2}
        matched.sort(
            key=lambda row: (
                priority[str(row["conflict_status"])],
                str(row["fact_key"]),
                str(row["fact_group_id"]),
            )
        )
        rows = matched[offset : offset + limit]
        return {
            "schema_version": "wechat-persona-fact-review-page-v1",
            "offset": offset,
            "limit": limit,
            "total": len(matched),
            "has_more": offset + len(rows) < len(matched),
            "rows": rows,
        }

    def group(self, fact_group_id: str) -> dict[str, Any]:
        detail = self._details.get(fact_group_id)
        if detail is None:
            raise FactReviewServerError("unknown fact_group_id")
        event = self.latest.get(fact_group_id)
        return {
            **detail,
            "review_status": event["review_status"] if event else "pending",
            "decision": dict(event) if event else None,
        }

    def _decision_event(
        self,
        payload: Mapping[str, Any],
        latest: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        group_id = str(payload.get("fact_group_id") or "")
        detail = self._details.get(group_id)
        group = self.by_id.get(group_id)
        if detail is None or group is None:
            raise FactReviewServerError("unknown fact_group_id")
        if payload.get("lineage_digest") != group["lineage_digest"]:
            raise FactReviewServerError("fact group lineage digest mismatch")
        status = str(payload.get("review_status") or "")
        if status not in REVIEW_STATUSES:
            raise FactReviewServerError("invalid review_status")
        reviewer_id = str(payload.get("reviewer_id") or "").strip()
        if not reviewer_id or len(reviewer_id) > 128:
            raise FactReviewServerError(
                "reviewer_id is required and must be at most 128 characters"
            )
        reason_code = str(payload.get("reason_code") or "")
        if reason_code not in REASON_CODES:
            raise FactReviewServerError("invalid reason_code")
        if status == "confirmed" and reason_code != "evidence_confirmed":
            raise FactReviewServerError(
                "confirmed decisions require evidence_confirmed"
            )
        if status != "confirmed" and reason_code == "evidence_confirmed":
            raise FactReviewServerError(
                "non-confirmed decisions require a non-confirmation reason"
            )
        selected = payload.get("selected_candidate_sha256")
        candidates = {
            candidate["candidate_sha256"]: candidate
            for candidate in detail["candidates"]
        }
        if status == "confirmed":
            if selected not in candidates:
                raise FactReviewServerError(
                    "confirmed decision must select a snapshot candidate"
                )
            if not candidates[str(selected)]["confirmation_eligible"]:
                raise FactReviewServerError(
                    "selected candidate is blocked by the confirmation policy"
                )
        elif selected is not None:
            raise FactReviewServerError(
                "non-confirmed decision must not select a candidate"
            )
        return {
            "fact_group_id": group_id,
            "lineage_digest": group["lineage_digest"],
            "review_status": status,
            "selected_candidate_sha256": selected if status == "confirmed" else None,
            "reason_code": reason_code,
            "reviewer_id": reviewer_id,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "revision": int(latest.get(group_id, {}).get("revision", 0)) + 1,
        }

    def _workspace_state(
        self, latest: Mapping[str, Mapping[str, Any]]
    ) -> dict[str, Any]:
        return {
            "schema_version": "wechat-persona-fact-review-workspace-v1",
            "snapshot_id": self.snapshot_id,
            "manifest_sha256": self.manifest_sha256,
            "manifest_file_sha256": self.manifest_file_sha256,
            "fact_ledger_sha256": self.ledger_sha256,
            "review_candidates_sha256": self.candidates_sha256,
            "training_eligible": False,
            "confirmed_cards_built": False,
            "rag_index_built": False,
            "formal_approval_required": True,
            "decisions": list(latest.values()),
        }

    def save_decisions(
        self, payloads: list[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        if not payloads:
            return []
        with self._lock:
            latest = dict(self.latest)
            events: list[dict[str, Any]] = []
            for payload in payloads:
                event = self._decision_event(payload, latest)
                latest[event["fact_group_id"]] = event
                events.append(event)
            audit_events = [
                {
                    **event,
                    "snapshot_id": self.snapshot_id,
                    "manifest_sha256": self.manifest_sha256,
                    "manifest_file_sha256": self.manifest_file_sha256,
                    "fact_ledger_sha256": self.ledger_sha256,
                    "review_candidates_sha256": self.candidates_sha256,
                }
                for event in events
            ]
            _append_audit_many(self._audit_path, audit_events)
            _atomic_json(self._latest_path, self._workspace_state(latest))
            self.latest = latest
        return events

    def save_decision(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.save_decisions([payload])[0]


class FactReviewApplication:
    def __init__(self, *, store: FactReviewStore, page_path: Path) -> None:
        self.store = store
        self.page_path = _regular(page_path.resolve(), "fact review HTML")
        if self.page_path.suffix.lower() != ".html":
            raise FactReviewServerError("fact review page must be an HTML file")

    def handler_class(self) -> type[BaseHTTPRequestHandler]:
        application = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "WechatPersonaFactReview/1"

            def _headers(
                self, status: HTTPStatus, content_type: str, length: int
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                    "connect-src 'self'; frame-ancestors 'none'; "
                    "base-uri 'none'; form-action 'self'",
                )
                self.end_headers()

            def _json(self, status: HTTPStatus, value: Mapping[str, Any]) -> None:
                data = (
                    json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
                ).encode("utf-8")
                self._headers(status, "application/json; charset=utf-8", len(data))
                self.wfile.write(data)

            def _error(self, status: HTTPStatus, message: str) -> None:
                self._json(status, {"status": "blocked", "error": message})

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path in {"", "/"}:
                    self.send_response(HTTPStatus.FOUND)
                    self.send_header("Location", "./fact-review.html")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return
                if parsed.path == "/fact-review.html":
                    data = application.page_path.read_bytes()
                    self._headers(
                        HTTPStatus.OK, "text/html; charset=utf-8", len(data)
                    )
                    self.wfile.write(data)
                    return
                if parsed.path == "/api/bootstrap":
                    self._json(HTTPStatus.OK, application.store.bootstrap())
                    return
                query = parse_qs(parsed.query)
                if parsed.path == "/api/groups":
                    try:
                        offset = int((query.get("offset") or ["0"])[0])
                        limit = int(
                            (query.get("limit") or [str(DEFAULT_PAGE_SIZE)])[0]
                        )
                        page = application.store.page(
                            offset=offset,
                            limit=limit,
                            status=(query.get("status") or ["pending"])[0],
                            conflict=(query.get("conflict") or ["all"])[0],
                            risk=(query.get("risk") or ["all"])[0],
                            search=(query.get("search") or [""])[0],
                        )
                    except (ValueError, FactReviewServerError) as exc:
                        self._error(HTTPStatus.BAD_REQUEST, str(exc))
                        return
                    self._json(HTTPStatus.OK, page)
                    return
                if parsed.path == "/api/group":
                    group_id = (query.get("fact_group_id") or [""])[0]
                    try:
                        group = application.store.group(group_id)
                    except FactReviewServerError as exc:
                        self._error(HTTPStatus.NOT_FOUND, str(exc))
                        return
                    self._json(HTTPStatus.OK, group)
                    return
                self._error(HTTPStatus.NOT_FOUND, "route not found")

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/api/decision":
                    self._error(HTTPStatus.NOT_FOUND, "route not found")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._error(HTTPStatus.BAD_REQUEST, "invalid content length")
                    return
                if length <= 0 or length > MAX_REQUEST_BYTES:
                    self._error(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        "invalid request size",
                    )
                    return
                try:
                    value = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(value, dict):
                        raise FactReviewServerError(
                            "request body must be an object"
                        )
                    event = application.store.save_decision(value)
                except (
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    FactReviewServerError,
                ) as exc:
                    self._error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        "status": "saved",
                        "event": event,
                        "training_eligible": False,
                        "confirmed_cards_built": False,
                        "rag_index_built": False,
                    },
                )

            def log_message(self, format_string: str, *args: object) -> None:
                message = format_string % args
                print(f"fact-review-http {self.client_address[0]} {message}")

        return Handler


def create_server(
    *, application: FactReviewApplication, host: str, port: int
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise FactReviewServerError(
            "fact review server must bind to a loopback address"
        )
    if port < 1024 or port > 65535:
        raise FactReviewServerError(
            "fact review server port must be in [1024, 65535]"
        )
    return ThreadingHTTPServer((host, port), application.handler_class())


__all__ = [
    "FactReviewApplication",
    "FactReviewServerError",
    "FactReviewStore",
    "create_server",
]
