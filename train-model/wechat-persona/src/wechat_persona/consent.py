"""Fail-closed consent ledger validation for private chat processing."""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._common import canonical_json, digest, parse_datetime


class ConsentError(ValueError):
    """Raised whenever processing scope cannot be proven."""


def load_consent(source: Path | str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return dict(source)
    path = Path(source)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConsentError("consent ledger cannot be read") from exc
    if not isinstance(value, dict):
        raise ConsentError("consent ledger must be an object")
    return value


def consent_digest(record: Mapping[str, Any]) -> str:
    """Return the immutable ledger digest without exposing its values."""
    return digest(dict(record))


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
    required = {str(item) for item in required_purposes}
    required_messages = {str(item) for item in required_message_types}
    required_media = {str(item) for item in required_media_types}
    missing = {key for key in ("consent_id", "subject_id", "adult_verified", "purposes", "scope", "status", "ledger_version", "verified_at") if key not in record}
    if missing:
        raise ConsentError(f"consent_missing: fields={','.join(sorted(missing))}")
    if record.get("adult_verified") is not True:
        raise ConsentError("consent_missing: adult verification is required")
    if subject_id and record.get("subject_id") != subject_id:
        raise ConsentError("consent_subject_mismatch")
    if record.get("status") != "verified":
        raise ConsentError(f"consent_{record.get('status', 'missing')}")
    purposes = set(record.get("purposes") or [])
    if not required.issubset(purposes):
        raise ConsentError("consent_scope_missing: purpose")
    scope = record.get("scope")
    if not isinstance(scope, Mapping):
        raise ConsentError("consent_scope_missing")
    if not required_messages.issubset(set(scope.get("message_types") or [])):
        raise ConsentError("consent_scope_missing: message type")
    if not required_media.issubset(set(scope.get("media_types") or [])):
        raise ConsentError("consent_scope_missing: media type")
    if not scope.get("withdrawal_key") or not scope.get("retention_until"):
        raise ConsentError("consent_scope_missing: retention or withdrawal key")
    if includes_third_party and scope.get("third_party_policy") != "separate_authorization":
        raise ConsentError("consent_scope_missing: third-party authorization")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    expiry = parse_datetime(scope.get("retention_until"))
    if expiry is None or expiry <= current.astimezone(timezone.utc):
        raise ConsentError("consent_expired")
    verified_at = parse_datetime(record.get("verified_at"))
    if verified_at is None:
        raise ConsentError("consent_missing: verified_at")
    start = parse_datetime(scope.get("time_start")) if scope.get("time_start") else None
    end = parse_datetime(scope.get("time_end")) if scope.get("time_end") else None
    if start and end and start > end:
        raise ConsentError("consent_scope_invalid: time range")
    # Return a copy annotated with an opaque digest.  The annotation is useful
    # to manifests; callers should never log the original record.
    checked = dict(record)
    checked["authorization_status"] = "verified"
    checked["consent_digest"] = consent_digest(record)
    return checked
