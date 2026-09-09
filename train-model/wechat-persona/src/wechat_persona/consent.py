"""Fail-closed consent ledger validation for private chat processing.

The returned authorization is deliberately a small, derived record.  Consent
ledger fields can contain controlled references and withdrawal identifiers and
must not be copied into dataset manifests or ordinary logs.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._common import digest, file_digest, parse_datetime


class ConsentError(ValueError):
    """Raised whenever processing scope cannot be proven."""


_PURPOSES = {"processing", "persona_style", "memory_rag", "evaluation", "screenplay"}
_MESSAGE_TYPES = {
    "text",
    "emoji",
    "quote",
    "link",
    "system",
    "payment",
    "call",
    "other",
}
_MEDIA_TYPES = {"image", "voice", "video", "file"}
_THIRD_PARTY_POLICIES = {"exclude", "separate_authorization"}
_STATUS_VALUES = {"pending", "verified", "expired", "withdrawn"}
_REQUIRED_FIELDS = {
    "consent_id",
    "subject_id",
    "adult_verified",
    "purposes",
    "scope",
    "status",
    "ledger_version",
    "verified_at",
}
_REQUIRED_SCOPE_FIELDS = {
    "message_types",
    "media_types",
    "time_start",
    "time_end",
    "third_party_policy",
    "retention_until",
    "withdrawal_key",
}
_OPTIONAL_FIELDS = {"evidence_ref"}


def load_consent(source: Path | str | Mapping[str, Any]) -> dict[str, Any]:
    """Load exactly one JSON object and reject duplicate object keys."""

    if isinstance(source, Mapping):
        return dict(source)
    path = Path(source)

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                raise ConsentError(f"consent ledger contains duplicate field: {key}")
            value[key] = child
        return value

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except UnicodeDecodeError as exc:
        raise ConsentError("consent ledger must be UTF-8 JSON") from exc
    except ConsentError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ConsentError("consent ledger cannot be read") from exc
    if not isinstance(value, dict):
        raise ConsentError("consent ledger must be an object")
    return value


def consent_digest(record: Mapping[str, Any]) -> str:
    """Return the immutable ledger digest without exposing its values."""
    return digest(dict(record))


def _string_set(value: Any, *, field: str, allow_empty: bool = False) -> set[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ConsentError(f"consent_scope_invalid: {field} must be a list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ConsentError(f"consent_scope_invalid: {field} contains an invalid value")
    normalized = [item.strip() for item in value]
    if len(normalized) != len(set(normalized)):
        raise ConsentError(f"consent_scope_invalid: {field} contains duplicates")
    return set(normalized)


def _required_values(values: Iterable[str], *, field: str) -> set[str]:
    if isinstance(values, (str, bytes, Mapping)):
        raise ConsentError(
            f"consent_scope_invalid: required {field} must be an iterable of strings"
        )
    try:
        materialized = list(values)
    except TypeError as exc:
        raise ConsentError(
            f"consent_scope_invalid: required {field} must be an iterable of strings"
        ) from exc
    if any(not isinstance(item, str) or not item.strip() for item in materialized):
        raise ConsentError(
            f"consent_scope_invalid: required {field} contains an invalid value"
        )
    normalized = [item.strip() for item in materialized]
    if len(normalized) != len(set(normalized)):
        raise ConsentError(
            f"consent_scope_invalid: required {field} contains duplicates"
        )
    return set(normalized)


def _require_nonempty_string(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConsentError(f"consent_missing: {key}")
    return value.strip()


def verify_consent(
    source: Path | str | Mapping[str, Any],
    *,
    required_purposes: Iterable[str] = (),
    required_message_types: Iterable[str] = (),
    required_media_types: Iterable[str] = (),
    subject_id: str | None = None,
    includes_third_party: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    record = load_consent(source)
    required = _required_values(required_purposes, field="purpose")
    required_messages = _required_values(required_message_types, field="message type")
    required_media = _required_values(required_media_types, field="media type")
    unknown_required_purposes = required - _PURPOSES
    if unknown_required_purposes:
        raise ConsentError("consent_scope_invalid: required purpose")
    missing = _REQUIRED_FIELDS - set(record)
    if missing:
        raise ConsentError(f"consent_missing: fields={','.join(sorted(missing))}")
    unknown = set(record) - _REQUIRED_FIELDS - _OPTIONAL_FIELDS
    if unknown:
        raise ConsentError(f"consent_scope_invalid: unknown fields={','.join(sorted(unknown))}")
    consent_id = _require_nonempty_string(record, "consent_id")
    _require_nonempty_string(record, "subject_id")
    _require_nonempty_string(record, "ledger_version")
    evidence_ref = record.get("evidence_ref")
    if evidence_ref is not None and (
        not isinstance(evidence_ref, str) or not evidence_ref.strip()
    ):
        raise ConsentError("consent_scope_invalid: evidence_ref")
    if record.get("adult_verified") is not True:
        raise ConsentError("consent_missing: adult verification is required")
    if subject_id and record.get("subject_id") != subject_id:
        raise ConsentError("consent_subject_mismatch")
    status = record.get("status")
    if status not in _STATUS_VALUES:
        raise ConsentError("consent_status_invalid")
    if status != "verified":
        raise ConsentError(f"consent_{status}")
    purposes = _string_set(record.get("purposes"), field="purposes")
    if not purposes.issubset(_PURPOSES):
        raise ConsentError("consent_scope_invalid: purpose")
    if not required.issubset(purposes):
        raise ConsentError("consent_scope_missing: purpose")
    scope = record.get("scope")
    if not isinstance(scope, Mapping):
        raise ConsentError("consent_scope_missing")
    missing_scope = _REQUIRED_SCOPE_FIELDS - set(scope)
    if missing_scope:
        raise ConsentError(f"consent_scope_missing: fields={','.join(sorted(missing_scope))}")
    unknown_scope = set(scope) - _REQUIRED_SCOPE_FIELDS
    if unknown_scope:
        raise ConsentError(
            f"consent_scope_invalid: unknown scope fields={','.join(sorted(unknown_scope))}"
        )
    message_types = _string_set(scope.get("message_types"), field="message_types")
    media_types = _string_set(scope.get("media_types"), field="media_types", allow_empty=True)
    if not message_types.issubset(_MESSAGE_TYPES):
        raise ConsentError("consent_scope_invalid: message type")
    if not media_types.issubset(_MEDIA_TYPES):
        raise ConsentError("consent_scope_invalid: media type")
    if not required_messages.issubset(_MESSAGE_TYPES):
        raise ConsentError("consent_scope_invalid: required message type")
    if not required_media.issubset(_MEDIA_TYPES):
        raise ConsentError("consent_scope_invalid: required media type")
    if not required_messages.issubset(message_types):
        raise ConsentError("consent_scope_missing: message type")
    if not required_media.issubset(media_types):
        raise ConsentError("consent_scope_missing: media type")
    if not isinstance(scope.get("withdrawal_key"), str) or not str(scope.get("withdrawal_key")).strip():
        raise ConsentError("consent_scope_missing: withdrawal key")
    if not isinstance(scope.get("retention_until"), str) or not str(scope.get("retention_until")).strip():
        raise ConsentError("consent_scope_missing: retention or withdrawal key")
    third_party_policy = scope.get("third_party_policy")
    if third_party_policy not in _THIRD_PARTY_POLICIES:
        raise ConsentError("consent_scope_invalid: third-party policy")
    if includes_third_party and third_party_policy != "separate_authorization":
        raise ConsentError("consent_scope_missing: third-party authorization")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    retention_until = scope.get("retention_until")
    if not isinstance(retention_until, str):
        raise ConsentError("consent_scope_invalid: retention_until")
    expiry = parse_datetime(retention_until)
    if expiry is None or expiry <= current.astimezone(timezone.utc):
        raise ConsentError("consent_expired")
    verified_at_value = record.get("verified_at")
    if not isinstance(verified_at_value, str):
        raise ConsentError("consent_scope_invalid: verified_at")
    verified_at = parse_datetime(verified_at_value)
    if verified_at is None:
        raise ConsentError("consent_missing: verified_at")
    if verified_at > current.astimezone(timezone.utc):
        raise ConsentError("consent_scope_invalid: verified_at is in the future")
    time_start = scope.get("time_start")
    time_end = scope.get("time_end")
    if time_start is not None and not isinstance(time_start, str):
        raise ConsentError("consent_scope_invalid: time_start")
    if time_end is not None and not isinstance(time_end, str):
        raise ConsentError("consent_scope_invalid: time_end")
    start = parse_datetime(time_start) if time_start else None
    end = parse_datetime(time_end) if time_end else None
    if time_start and start is None:
        raise ConsentError("consent_scope_invalid: time_start")
    if time_end and end is None:
        raise ConsentError("consent_scope_invalid: time_end")
    if start and end and start > end:
        raise ConsentError("consent_scope_invalid: time range")
    checked = {
        "consent_id": consent_id,
        "authorization_status": "verified",
        "consent_digest": consent_digest(record),
        "purposes": sorted(purposes),
        "scope": {
            "message_types": sorted(message_types),
            "media_types": sorted(media_types),
            "time_start": time_start,
            "time_end": time_end,
            "third_party_policy": third_party_policy,
        },
    }
    if not isinstance(source, Mapping):
        try:
            checked["consent_file_sha256"] = file_digest(Path(source))
        except (OSError, TypeError) as exc:
            raise ConsentError("consent ledger cannot be read") from exc
    return checked


def consent_file_digest(source: Path | str) -> str:
    """Return the byte-level digest for a controlled consent file."""

    try:
        return file_digest(Path(source))
    except (OSError, TypeError) as exc:
        raise ConsentError("consent ledger cannot be read") from exc
