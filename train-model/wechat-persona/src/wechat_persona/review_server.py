"""Loopback-only review service for one immutable curated dataset draft."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
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

from ._common import file_digest
from .curation import CONTROLLED_ROOT, CURATION_SCHEMA_VERSION
from .redact import scan_candidate
from .review import STATUSES, apply_review


class ReviewServerError(ValueError):
    pass


SPLITS = ("train", "validation")
MAX_REQUEST_BYTES = 1024 * 1024
MAX_EDITED_REPLY_CHARS = 8000
DEFAULT_PAGE_SIZE = 32
MAX_PAGE_SIZE = 100
MAX_SEARCH_CHARS = 200
ALLOWED_LABELS = {
    "direct_response",
    "emotional_attunement",
    "caring_followup",
    "practical_help",
    "playful_affection",
    "relationship_context",
    "repair_boundary",
}
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewServerError(f"{label} must be readable UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ReviewServerError(f"{label} must contain an object")
    return value


def _below(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    controlled = root.resolve()
    if resolved == controlled or controlled not in resolved.parents:
        raise ReviewServerError(f"{label} must be below {controlled}")
    return resolved


def _regular(path: Path, label: str) -> Path:
    if not path.is_file() or path.is_symlink():
        raise ReviewServerError(f"{label} must be a regular non-symlink file")
    return path


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


def _atomic_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=stat.S_IRWXU)
    path.parent.chmod(stat.S_IRWXU)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary_name).chmod(stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _append_audit(path: Path, event: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=stat.S_IRWXU)
    path.parent.chmod(stat.S_IRWXU)
    with path.open("a", encoding="utf-8") as handle:
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


def _load_rows(path: Path, split: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReviewServerError(
                    f"invalid {split} JSONL at line {line_number}"
                ) from exc
            if not isinstance(row, dict):
                raise ReviewServerError(f"invalid {split} row at line {line_number}")
            metadata = row.get("metadata")
            if (
                not isinstance(metadata, dict)
                or metadata.get("split") != split
                or not isinstance(row.get("sample_id"), str)
                or not row["sample_id"]
                or not isinstance(row.get("session_id"), str)
                or not isinstance(row.get("messages"), list)
            ):
                raise ReviewServerError(
                    f"invalid {split} candidate contract at line {line_number}"
                )
            rows.append(row)
    return rows


@dataclass(frozen=True)
class ReviewDatasetIdentity:
    curation_id: str
    curation_digest: str
    manifest_file_sha256: str
    selection_sha256: str
    counts: dict[str, int]


class ReviewStore:
    """Validated dataset reader and append-only human-decision workspace."""

    def __init__(
        self,
        *,
        dataset_dir: Path,
        review_dir: Path,
        controlled_root: Path = CONTROLLED_ROOT,
    ) -> None:
        self.dataset_dir = _below(dataset_dir, controlled_root, "dataset directory")
        self.review_dir = _below(review_dir, controlled_root, "review directory")
        if self.review_dir == self.dataset_dir or self.dataset_dir in self.review_dir.parents:
            raise ReviewServerError("review directory must not be inside the dataset draft")
        if self.review_dir.exists() and (
            not self.review_dir.is_dir() or self.review_dir.is_symlink()
        ):
            raise ReviewServerError("review directory must be a non-symlink directory")
        self.review_dir.mkdir(parents=True, exist_ok=True, mode=stat.S_IRWXU)
        self.review_dir.chmod(stat.S_IRWXU)
        self.manifest_path = _regular(
            self.dataset_dir / "selection-manifest.json", "selection manifest"
        )
        self.manifest = _read_json(self.manifest_path, "selection manifest")
        self._split_paths = {
            split: _regular(self.dataset_dir / f"{split}.jsonl", f"{split} split")
            for split in SPLITS
        }
        self.identity = self._validate_identity()
        self.rows = {
            split: _load_rows(self._split_paths[split], split) for split in SPLITS
        }
        if {split: len(self.rows[split]) for split in SPLITS} != self.identity.counts:
            raise ReviewServerError("selected split counts do not match the manifest")
        self.by_id: dict[str, dict[str, Any]] = {}
        for split in SPLITS:
            for row in self.rows[split]:
                sample_id = str(row["sample_id"])
                if sample_id in self.by_id:
                    raise ReviewServerError("duplicate sample ID across curated splits")
                self.by_id[sample_id] = row
        self._lock = threading.Lock()
        self._latest_path = self.review_dir / "latest-decisions.json"
        self._audit_path = self.review_dir / "review-events.audit.jsonl"
        self._redacted_path = self.review_dir / "latest-reviewed-rows.jsonl"
        self.latest, self.redacted_rows = self._load_existing()

    def _validate_identity(self) -> ReviewDatasetIdentity:
        manifest = self.manifest
        if manifest.get("schema_version") != CURATION_SCHEMA_VERSION:
            raise ReviewServerError("unsupported curated dataset schema")
        if (
            manifest.get("parent_test_untouched") is not True
            or manifest.get("test_artifact_created") is not False
            or manifest.get("formal_training_eligible") is not False
        ):
            raise ReviewServerError("curated draft governance boundary is invalid")
        curation_id = str(manifest.get("curation_id") or "")
        curation_digest = str(manifest.get("curation_digest") or "")
        selection_sha256 = str(manifest.get("selection_sha256") or "")
        if not curation_id or not _DIGEST_RE.fullmatch(curation_digest):
            raise ReviewServerError("curation identity is missing")
        if not _DIGEST_RE.fullmatch(selection_sha256):
            raise ReviewServerError("selection identity is missing")
        declared = manifest.get("artifacts_sha256") or {}
        counts = manifest.get("selected_counts") or {}
        normalized_counts: dict[str, int] = {}
        for split in SPLITS:
            observed = file_digest(self._split_paths[split])
            if declared.get(f"{split}.jsonl") != observed:
                raise ReviewServerError(f"{split} artifact digest mismatch")
            count = counts.get(split)
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                raise ReviewServerError(f"invalid selected count for {split}")
            normalized_counts[split] = count
        return ReviewDatasetIdentity(
            curation_id=curation_id,
            curation_digest=curation_digest,
            manifest_file_sha256=file_digest(self.manifest_path),
            selection_sha256=selection_sha256,
            counts=normalized_counts,
        )

    def _load_existing(
        self,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        if not self._latest_path.exists():
            return {}, {}
        state = _read_json(self._latest_path, "latest review state")
        if (
            state.get("curation_digest") != self.identity.curation_digest
            or state.get("manifest_file_sha256")
            != self.identity.manifest_file_sha256
        ):
            raise ReviewServerError("existing review state belongs to another dataset")
        decisions = state.get("decisions")
        if not isinstance(decisions, list):
            raise ReviewServerError("existing review decisions are invalid")
        latest: dict[str, dict[str, Any]] = {}
        for event in decisions:
            if not isinstance(event, dict) or event.get("sample_id") not in self.by_id:
                raise ReviewServerError("existing review decision has an unknown sample")
            latest[str(event["sample_id"])] = event
        redacted_rows: dict[str, dict[str, Any]] = {}
        if self._redacted_path.exists():
            for row in _load_rows_for_review(self._redacted_path):
                redacted_rows[str(row["sample_id"])] = row
        return latest, redacted_rows

    def bootstrap(self) -> dict[str, Any]:
        counts = {status: 0 for status in sorted(STATUSES)}
        counts["uncertain"] = len(self.by_id)
        for event in self.latest.values():
            status = str(event.get("review_status") or "")
            counts["uncertain"] = max(0, counts["uncertain"] - 1)
            if status in counts:
                counts[status] += 1
        return {
            "schema_version": "wechat-persona-review-service-v1",
            "dataset": {
                "curation_id": self.identity.curation_id,
                "curation_digest": self.identity.curation_digest,
                "manifest_file_sha256": self.identity.manifest_file_sha256,
                "selection_sha256": self.identity.selection_sha256,
                "counts": self.identity.counts,
                "allowed_splits": list(SPLITS),
                "test_access": "blocked",
            },
            "review": {
                "decision_count": len(self.latest),
                "status_counts": counts,
                "training_eligible": False,
                "formal_approval_required": True,
            },
            "decisions": list(self.latest.values()),
        }

    def split_path(self, split: str) -> Path:
        if split not in SPLITS:
            raise ReviewServerError("only train and validation are available for review")
        return self._split_paths[split]

    def page(
        self,
        *,
        split: str,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
        status: str = "all",
        search: str = "",
    ) -> dict[str, Any]:
        """Return one bounded page; candidate text never leaves this page."""
        if split != "all" and split not in SPLITS:
            raise ReviewServerError("only train and validation are available for review")
        if offset < 0:
            raise ReviewServerError("offset must be non-negative")
        if limit < 1 or limit > MAX_PAGE_SIZE:
            raise ReviewServerError(f"limit must be in [1, {MAX_PAGE_SIZE}]")
        if status != "all" and status not in STATUSES:
            raise ReviewServerError("invalid review status filter")
        search = str(search or "").strip()
        if len(search) > MAX_SEARCH_CHARS:
            raise ReviewServerError("search query is too long")
        needle = search.casefold()
        selected_splits = SPLITS if split == "all" else (split,)
        matched: list[dict[str, Any]] = []
        for selected_split in selected_splits:
            for row in self.rows[selected_split]:
                row_status = str(
                    self.latest.get(str(row["sample_id"]), {}).get(
                        "review_status", "uncertain"
                    )
                )
                if status != "all" and row_status != status:
                    continue
                if needle and needle not in json.dumps(
                    row, ensure_ascii=False, separators=(",", ":")
                ).casefold():
                    continue
                matched.append(row)
        page_rows = matched[offset : offset + limit]
        return {
            "schema_version": "wechat-persona-review-page-v1",
            "split": split,
            "offset": offset,
            "limit": limit,
            "total": len(matched),
            "has_more": offset + len(page_rows) < len(matched),
            "rows": page_rows,
        }

    def save_decision(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        sample_id = str(payload.get("sample_id") or "")
        row = self.by_id.get(sample_id)
        if row is None:
            raise ReviewServerError("unknown sample_id")
        status = str(payload.get("review_status") or "")
        if status not in STATUSES:
            raise ReviewServerError("invalid review_status")
        reviewer_id = str(payload.get("reviewer_id") or "").strip()
        if not reviewer_id or len(reviewer_id) > 128:
            raise ReviewServerError("reviewer_id is required and must be at most 128 characters")
        reason_value = payload.get("review_reason")
        reason = str(reason_value).strip() if reason_value is not None else ""
        if len(reason) > 512:
            raise ReviewServerError("review_reason is too long")
        if status in {"reject", "redact_keep"} and not reason:
            raise ReviewServerError("reject and redact_keep require a reason")
        notes_value = payload.get("notes")
        notes = str(notes_value).strip() if notes_value is not None else ""
        if len(notes) > 2000:
            raise ReviewServerError("notes is too long")
        expected_hash = str((row.get("metadata") or {}).get("content_sha256") or "")
        if payload.get("content_sha256") != expected_hash or not _DIGEST_RE.fullmatch(
            expected_hash
        ):
            raise ReviewServerError("candidate content digest mismatch")
        labels = payload.get("labels") or []
        if (
            not isinstance(labels, list)
            or any(label not in ALLOWED_LABELS for label in labels)
            or len(set(labels)) != len(labels)
        ):
            raise ReviewServerError("invalid usefulness labels")
        reviewed_at = datetime.now(timezone.utc).isoformat()
        edited_reply = str(payload.get("edited_reply") or "").strip()
        reviewed_row: dict[str, Any] | None = None
        redacted_content_sha256: str | None = None
        if status == "redact_keep":
            if not edited_reply or len(edited_reply) > MAX_EDITED_REPLY_CHARS:
                raise ReviewServerError("edited_reply is required and too long")
            messages = [dict(message) for message in row["messages"]]
            assistant_indices = [
                index
                for index, message in enumerate(messages)
                if message.get("role") == "assistant"
            ]
            if not assistant_indices or assistant_indices[-1] != len(messages) - 1:
                raise ReviewServerError("candidate does not end with an assistant message")
            messages[-1]["content"] = edited_reply
            reviewed_row = apply_review(
                row,
                status,
                reviewer_id=reviewer_id,
                reason=reason,
                edited_messages=messages,
                reviewed_at=reviewed_at,
            )
            if scan_candidate(reviewed_row).get("hard_leak_count") != 0:
                raise ReviewServerError("edited row failed the privacy scan")
            redacted_content_sha256 = str(
                (reviewed_row.get("metadata") or {}).get("redacted_content_sha256")
                or ""
            )
        elif edited_reply:
            raise ReviewServerError("edited_reply is valid only for redact_keep")
        event = {
            "sample_id": sample_id,
            "session_id": str(row.get("session_id") or ""),
            "review_status": status,
            "reviewer_id": reviewer_id,
            "reviewed_at": reviewed_at,
            "review_reason": reason or None,
            "notes": notes or None,
            "content_sha256": expected_hash,
            "redacted_content_sha256": redacted_content_sha256,
            "labels": sorted(labels),
            "revision": int(self.latest.get(sample_id, {}).get("revision", 0)) + 1,
        }
        with self._lock:
            _append_audit(
                self._audit_path,
                {
                    **event,
                    "curation_digest": self.identity.curation_digest,
                    "manifest_file_sha256": self.identity.manifest_file_sha256,
                },
            )
            self.latest[sample_id] = event
            if reviewed_row is None:
                self.redacted_rows.pop(sample_id, None)
            else:
                self.redacted_rows[sample_id] = reviewed_row
            _atomic_json(
                self._latest_path,
                {
                    "schema_version": "wechat-persona-review-workspace-v1",
                    "curation_id": self.identity.curation_id,
                    "curation_digest": self.identity.curation_digest,
                    "manifest_file_sha256": self.identity.manifest_file_sha256,
                    "selection_sha256": self.identity.selection_sha256,
                    "training_eligible": False,
                    "formal_approval_required": True,
                    "decisions": list(self.latest.values()),
                },
            )
            _atomic_jsonl(
                self._redacted_path,
                list(self.redacted_rows.values()),
            )
        return event


def _load_rows_for_review(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReviewServerError(
                    f"invalid reviewed row JSONL at line {line_number}"
                ) from exc
            if not isinstance(row, dict) or not row.get("sample_id"):
                raise ReviewServerError("invalid persisted reviewed row")
            rows.append(row)
    return rows


class ReviewApplication:
    def __init__(self, *, store: ReviewStore, page_path: Path) -> None:
        self.store = store
        self.page_path = _regular(page_path.resolve(), "review HTML")
        if self.page_path.suffix.lower() != ".html":
            raise ReviewServerError("review page must be an HTML file")

    def handler_class(self) -> type[BaseHTTPRequestHandler]:
        application = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "WechatPersonaReview/1"

            def _headers(self, status: HTTPStatus, content_type: str, length: int) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Frame-Options", "SAMEORIGIN")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                    "connect-src 'self'; frame-ancestors 'self'",
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
                    self.send_header("Location", "./review.html")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return
                if parsed.path == "/review.html":
                    data = application.page_path.read_bytes()
                    self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(data))
                    self.wfile.write(data)
                    return
                if parsed.path == "/api/bootstrap":
                    self._json(HTTPStatus.OK, application.store.bootstrap())
                    return
                if parsed.path == "/api/dataset":
                    query = parse_qs(parsed.query)
                    split = (query.get("split") or ["all"])[0]
                    try:
                        offset = int((query.get("offset") or ["0"])[0])
                        limit = int(
                            (query.get("limit") or [str(DEFAULT_PAGE_SIZE)])[0]
                        )
                    except ValueError:
                        self._error(HTTPStatus.BAD_REQUEST, "offset and limit must be integers")
                        return
                    try:
                        page = application.store.page(
                            split=split,
                            offset=offset,
                            limit=limit,
                            status=(query.get("status") or ["all"])[0],
                            search=(query.get("search") or [""])[0],
                        )
                    except ReviewServerError as exc:
                        forbidden = split not in {"all", *SPLITS}
                        self._error(
                            HTTPStatus.FORBIDDEN if forbidden else HTTPStatus.BAD_REQUEST,
                            str(exc),
                        )
                        return
                    self._json(HTTPStatus.OK, page)
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
                    self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "invalid request size")
                    return
                try:
                    value = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(value, dict):
                        raise ReviewServerError("request body must be an object")
                    event = application.store.save_decision(value)
                except (
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    ReviewServerError,
                ) as exc:
                    self._error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        "status": "saved",
                        "event": event,
                        "training_eligible": False,
                    },
                )

            def log_message(self, format_string: str, *args: object) -> None:
                message = format_string % args
                print(f"review-http {self.client_address[0]} {message}")

        return Handler


def create_server(
    *, application: ReviewApplication, host: str, port: int
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ReviewServerError("review server must bind to a loopback address")
    if port < 1024 or port > 65535:
        raise ReviewServerError("review server port must be in [1024, 65535]")
    return ThreadingHTTPServer((host, port), application.handler_class())


__all__ = [
    "ReviewApplication",
    "ReviewServerError",
    "ReviewStore",
    "create_server",
]
