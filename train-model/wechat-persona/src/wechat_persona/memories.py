"""Contracts and builders for removable, owner-scoped relationship memories.

This module deliberately contains no model or network dependency.  Memory cards are
the auditable boundary between approved redacted candidates and a retrieval index.
Only manually approved, confirmed cards are eligible for the default index; high
sensitivity cards are retained in the manifest input but excluded by default.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence


class MemoryContractError(ValueError):
    """Raised when a memory card or memory manifest violates the contract."""


MEMORY_STATUSES = frozenset({"candidate", "confirmed", "superseded", "deleted"})
SENSITIVITY_LEVELS = frozenset({"normal", "sensitive", "high"})
APPROVED_REVIEW_STATUSES = frozenset({"keep", "redact_keep", "approved", "confirmed"})
REJECTED_CARD_STATUSES = frozenset({"superseded", "deleted"})


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise MemoryContractError(f"invalid memory timestamp: {value!r}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MemoryContractError(f"invalid memory timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise MemoryContractError("memory timestamps must include a timezone")
    return parsed


def _sha256_payload(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class MemoryCard:
    memory_id: str
    owner_scope: str
    content: str
    source_session_ids: tuple[str, ...]
    created_at: str
    source_message_ids: tuple[str, ...] = ()
    valid_from: str | None = None
    valid_to: str | None = None
    confidence: float = 1.0
    status: str = "confirmed"
    sensitivity: str = "normal"
    fact_key: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)
    extractor_version: str = "unknown"
    human_review: Mapping[str, Any] | None = None
    lineage_digest: str | None = None
    # ``revision`` is an audit convenience.  The JSON schema represents the
    # ordering through valid_from/created_at/memory_id and therefore does not
    # serialize this compatibility field as an additional property.
    revision: int = 1

    def __post_init__(self) -> None:
        if not self.memory_id or not self.owner_scope or not self.content.strip():
            raise MemoryContractError("memory_id, owner_scope and content are required")
        if not self.source_session_ids or any(not item for item in self.source_session_ids):
            raise MemoryContractError("at least one source_session_id is required")
        if any(not item for item in self.source_message_ids):
            raise MemoryContractError("source_message_ids must not contain empty values")
        if self.status not in MEMORY_STATUSES:
            raise MemoryContractError(f"unsupported memory status: {self.status}")
        if self.sensitivity not in SENSITIVITY_LEVELS:
            raise MemoryContractError(f"unsupported memory sensitivity: {self.sensitivity}")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise MemoryContractError("memory confidence must be in [0, 1]")
        if self.revision < 1:
            raise MemoryContractError("memory revision must be positive")
        created = _parse_time(self.created_at)
        valid_from = _parse_time(self.valid_from)
        valid_to = _parse_time(self.valid_to)
        if created is None:
            raise MemoryContractError("created_at is required")
        if valid_from and valid_to and valid_to < valid_from:
            raise MemoryContractError("valid_to precedes valid_from")
        if self.lineage_digest is not None and not re.fullmatch(r"[a-f0-9]{64}", self.lineage_digest):
            raise MemoryContractError("lineage_digest must be a SHA-256 hex digest")

    @property
    def revision_key(self) -> tuple[datetime, datetime, str]:
        valid = _parse_time(self.valid_from) or _parse_time(self.created_at)
        created = _parse_time(self.created_at)
        assert valid is not None and created is not None
        return valid, created, self.memory_id

    def is_active(self, now: str | None = None) -> bool:
        """Return whether this card is eligible at *now* under default policy."""

        if self.status != "confirmed" or self.sensitivity == "high":
            return False
        moment = _parse_time(now or utc_now())
        assert moment is not None
        start = _parse_time(self.valid_from)
        end = _parse_time(self.valid_to)
        return (start is None or start <= moment) and (end is None or moment < end)

    @property
    def computed_lineage_digest(self) -> str:
        return _sha256_payload(
            {
                "memory_id": self.memory_id,
                "owner_scope": self.owner_scope,
                "source_session_ids": list(self.source_session_ids),
                "source_message_ids": list(self.source_message_ids),
                "content": self.content,
                "created_at": self.created_at,
                "extractor_version": self.extractor_version,
            }
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialize to the memory-card JSON schema (without compatibility fields)."""

        return {
            "memory_id": self.memory_id,
            "owner_scope": self.owner_scope,
            "content": self.content,
            "source_session_ids": list(self.source_session_ids),
            "source_message_ids": list(self.source_message_ids),
            "created_at": self.created_at,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "confidence": float(self.confidence),
            "status": self.status,
            "sensitivity": self.sensitivity,
            "fact_key": self.fact_key,
            "attributes": dict(self.attributes),
            "extractor_version": self.extractor_version,
            "human_review": dict(self.human_review) if self.human_review is not None else None,
            "lineage_digest": self.lineage_digest or self.computed_lineage_digest,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MemoryCard":
        values = dict(payload)
        values["source_session_ids"] = tuple(values.get("source_session_ids") or ())
        values["source_message_ids"] = tuple(values.get("source_message_ids") or ())
        values.setdefault("attributes", {})
        values.setdefault("extractor_version", "unknown")
        return cls(**{key: values[key] for key in cls.__dataclass_fields__ if key in values})


@dataclass(frozen=True)
class MemoryManifest:
    cards: tuple[MemoryCard, ...]
    consent_scope: str
    extractor_version: str
    schema_version: str = "memory-card-v1"
    manifest_digest: str = ""

    def __post_init__(self) -> None:
        if not self.consent_scope:
            raise MemoryContractError("consent_scope is required")
        if not self.extractor_version:
            raise MemoryContractError("extractor_version is required")
        digest = _sha256_payload([card.as_dict() for card in self.cards])
        if self.manifest_digest and self.manifest_digest != digest:
            raise MemoryContractError("memory manifest digest does not match cards")
        object.__setattr__(self, "manifest_digest", digest)

    @property
    def card_count(self) -> int:
        return len(self.cards)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "consent_scope": self.consent_scope,
            "extractor_version": self.extractor_version,
            "card_count": self.card_count,
            "manifest_digest": self.manifest_digest,
            "cards": [card.as_dict() for card in self.cards],
        }


def make_memory_id(owner_scope: str, content: str, source_message_ids: Sequence[str]) -> str:
    payload = "\x1f".join([owner_scope, content, *source_message_ids])
    return "memory_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _candidate_value(candidate: Any, *names: str, default: Any = None) -> Any:
    if isinstance(candidate, Mapping):
        for name in names:
            if name in candidate:
                return candidate[name]
    else:
        for name in names:
            if hasattr(candidate, name):
                return getattr(candidate, name)
    return default


def build_memory_cards(
    messages: Iterable[Mapping[str, Any] | Any],
    consent_scope: str,
    extractor_version: str,
    *,
    include_high_sensitivity: bool = False,
) -> MemoryManifest:
    """Build confirmed cards from manually approved, redacted candidates.

    Candidate rows are intentionally permissive about field names so that the
    importer/review layer can pass either dicts or lightweight dataclass objects.
    Unapproved rows are ignored; malformed approved rows fail closed.
    """

    if not consent_scope or not extractor_version:
        raise MemoryContractError("consent_scope and extractor_version are required")
    cards: list[MemoryCard] = []
    for candidate in messages:
        review_status = str(_candidate_value(candidate, "review_status", "human_review_status", default="uncertain"))
        if review_status not in APPROVED_REVIEW_STATUSES:
            continue
        if str(_candidate_value(candidate, "status", default="candidate")) in REJECTED_CARD_STATUSES:
            continue
        sensitivity = str(_candidate_value(candidate, "sensitivity", default="normal"))
        if sensitivity == "high" and not include_high_sensitivity:
            continue
        content = _candidate_value(candidate, "content", "text_redacted", "text")
        owner_scope = _candidate_value(candidate, "owner_scope", "owner")
        session_ids = _candidate_value(candidate, "source_session_ids", default=None)
        if session_ids is None:
            session_id = _candidate_value(candidate, "session_id", default=None)
            session_ids = [session_id] if session_id else []
        message_ids = _candidate_value(candidate, "source_message_ids", default=None)
        if message_ids is None:
            message_id = _candidate_value(candidate, "message_id", default=None)
            message_ids = [message_id] if message_id else []
        session_ids = tuple(str(value) for value in session_ids if value)
        message_ids = tuple(str(value) for value in message_ids if value)
        if not content or not owner_scope or not session_ids:
            raise MemoryContractError("approved memory candidates require owner, content and source session")
        memory_id = _candidate_value(candidate, "memory_id", default=None) or make_memory_id(str(owner_scope), str(content), message_ids)
        created_at = _candidate_value(candidate, "created_at", "timestamp", default=None)
        if created_at is None:
            raise MemoryContractError(f"approved memory {memory_id} is missing created_at")
        review = _candidate_value(candidate, "human_review", default=None)
        if review is None:
            review = {"status": review_status} if review_status else None
        card = MemoryCard(
            memory_id=str(memory_id),
            owner_scope=str(owner_scope),
            content=str(content).strip(),
            source_session_ids=session_ids,
            source_message_ids=message_ids,
            created_at=str(created_at),
            valid_from=_candidate_value(candidate, "valid_from", default=None),
            valid_to=_candidate_value(candidate, "valid_to", default=None),
            confidence=float(_candidate_value(candidate, "confidence", default=1.0)),
            status="confirmed",
            sensitivity=sensitivity,
            fact_key=_candidate_value(candidate, "fact_key", default=None),
            attributes=_candidate_value(candidate, "attributes", default={}) or {},
            extractor_version=extractor_version,
            human_review=review,
            lineage_digest=_candidate_value(candidate, "lineage_digest", default=None),
            revision=int(_candidate_value(candidate, "revision", default=1)),
        )
        cards.append(card)
    cards.sort(key=lambda item: (item.owner_scope, item.revision_key, item.memory_id))
    return MemoryManifest(tuple(cards), consent_scope=consent_scope, extractor_version=extractor_version)


__all__ = [
    "MemoryCard",
    "MemoryContractError",
    "MemoryManifest",
    "MEMORY_STATUSES",
    "SENSITIVITY_LEVELS",
    "build_memory_cards",
    "make_memory_id",
    "utc_now",
]
