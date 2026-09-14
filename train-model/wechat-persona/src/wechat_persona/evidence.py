"""Build authorized source-evidence chunks and review-only event candidates."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable, Mapping

from .memories import MemoryCard, make_memory_id


EVIDENCE_EXTRACTOR_VERSION = "wechat-source-evidence-v1"
EVENT_EXTRACTOR_VERSION = "wechat-relationship-event-rules-v1"

_RELATIONSHIP_START_PATTERNS = (
    re.compile(r"(?:我们|咱们|我俩|两个人).{0,16}(?:正式)?在一起"),
    re.compile(r"(?:在一起|恋爱).{0,10}(?:第一天|纪念日)"),
    re.compile(r"(?:确定|确认).{0,8}(?:关系|恋爱关系)"),
    re.compile(r"(?:成为|做).{0,8}(?:男朋友|女朋友|对象)"),
)
_EXPLICIT_RELATIVE_MARKERS = ("今天", "第一天", "从今天", "刚刚", "现在开始")
_ISO_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?(?!\d)")
_MONTH_DAY_RE = re.compile(r"(?<!\d)(\d{1,2})月(\d{1,2})日")


def _date_only(value: str) -> str:
    return value[:10]


def _split_lookup(split_manifest: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(session_id): str(split)
        for split, values in (split_manifest.get("session_ids_by_split") or {}).items()
        for session_id in values
    }


def _message_lines(session: Mapping[str, Any]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for turn in session.get("turns") or ():
        role = str(turn.get("speaker_role") or "unknown")
        message_ids = list(turn.get("message_ids") or ())
        texts = list(turn.get("message_contents") or ())
        timestamps = list(turn.get("timestamps") or ())
        if not texts:
            texts = [turn.get("text_redacted")]
        for index, text in enumerate(texts):
            if not text:
                continue
            timestamp = str(timestamps[index] if index < len(timestamps) else session.get("start_time") or "")
            message_id = str(message_ids[index] if index < len(message_ids) else "")
            lines.append({
                "text": str(text).strip(),
                "timestamp": timestamp,
                "message_id": message_id,
                "speaker_role": role,
            })
    return lines


def build_source_evidence_cards(
    sessions: Iterable[Mapping[str, Any]],
    *,
    owner_scope: str,
    split_manifest: Mapping[str, Any],
    max_chars: int = 700,
    max_messages: int = 12,
) -> list[MemoryCard]:
    """Convert redacted source messages into bounded, chronological evidence chunks.

    These cards assert only that an authorized redacted record exists. They do
    not promote inferred facts; derived event candidates remain ``candidate``.
    """

    if not re.fullmatch(r"owner_[a-f0-9]{24}", owner_scope):
        raise ValueError("owner_scope must be an opaque owner_<24 hex> identifier")
    if max_chars < 128 or max_messages < 1:
        raise ValueError("evidence chunk limits are too small")
    split_lookup = _split_lookup(split_manifest)
    cards: list[MemoryCard] = []
    for session in sessions:
        session_id = str(session.get("session_id") or "")
        if not session_id:
            raise ValueError("source session is missing session_id")
        current: list[dict[str, Any]] = []
        current_chars = 0

        def flush() -> None:
            nonlocal current, current_chars
            if not current:
                return
            start_time = str(current[0]["timestamp"] or session.get("start_time") or "")
            end_time = str(current[-1]["timestamp"] or session.get("end_time") or start_time)
            if not start_time:
                raise ValueError(f"source session is missing timestamp: {session_id}")
            content_lines = [f"记录日期：{_date_only(start_time)}"]
            content_lines.extend(
                f"{item['speaker_role']}：{item['text']}" for item in current
            )
            content = "\n".join(content_lines)
            message_ids = tuple(item["message_id"] for item in current if item["message_id"])
            memory_id = make_memory_id(owner_scope, content, message_ids)
            cards.append(MemoryCard(
                memory_id=memory_id,
                owner_scope=owner_scope,
                content=content,
                source_session_ids=(session_id,),
                source_message_ids=message_ids,
                created_at=start_time,
                confidence=1.0,
                status="confirmed",
                sensitivity="sensitive",
                attributes={
                    "record_kind": "authorized_source_evidence",
                    "source_start_time": start_time,
                    "source_end_time": end_time,
                    "original_split": split_lookup.get(session_id, "unknown"),
                    "truth_status": "unverified_conversation_evidence",
                },
                extractor_version=EVIDENCE_EXTRACTOR_VERSION,
                human_review={
                    "status": "not_applicable",
                    "basis": "authorized_redacted_source",
                },
            ))
            current = []
            current_chars = 0

        for item in _message_lines(session):
            item_chars = len(item["text"]) + len(item["speaker_role"]) + 2
            if current and (current_chars + item_chars > max_chars or len(current) >= max_messages):
                flush()
            current.append(item)
            current_chars += item_chars
        flush()
    cards.sort(key=lambda card: (card.created_at, card.memory_id))
    return cards


def _explicit_event_date(text: str, contextual_date: str) -> tuple[str, bool]:
    for match in _ISO_DATE_RE.finditer(text):
        year, month, day = (int(value) for value in match.groups())
        try:
            return datetime(year, month, day).date().isoformat(), True
        except ValueError:
            continue
    contextual_year = int(contextual_date[:4])
    for match in _MONTH_DAY_RE.finditer(text):
        month, day = (int(value) for value in match.groups())
        try:
            return datetime(contextual_year, month, day).date().isoformat(), True
        except ValueError:
            continue
    return contextual_date, False


def extract_relationship_event_candidates(cards: Iterable[MemoryCard]) -> list[MemoryCard]:
    """Extract conservative relationship-start candidates without confirming them."""

    candidates: list[MemoryCard] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for evidence in cards:
        if evidence.attributes.get("record_kind") != "authorized_source_evidence":
            continue
        body = evidence.content.split("\n", 1)[-1]
        if not any(pattern.search(body) for pattern in _RELATIONSHIP_START_PATTERNS):
            continue
        contextual_date = _date_only(evidence.created_at)
        event_date, explicit_date = _explicit_event_date(body, contextual_date)
        key = (event_date, evidence.source_message_ids)
        if key in seen:
            continue
        seen.add(key)
        explicit_relative = any(marker in body for marker in _EXPLICIT_RELATIVE_MARKERS)
        confidence = 0.9 if explicit_date and explicit_relative else 0.75 if explicit_date else 0.65 if explicit_relative else 0.45
        content = f"候选事实：双方关系开始日期可能是 {event_date}。"
        candidates.append(MemoryCard(
            memory_id=make_memory_id(evidence.owner_scope, content, evidence.source_message_ids),
            owner_scope=evidence.owner_scope,
            content=content,
            source_session_ids=evidence.source_session_ids,
            source_message_ids=evidence.source_message_ids,
            created_at=evidence.created_at,
            confidence=confidence,
            status="candidate",
            sensitivity="sensitive",
            fact_key="relationship.started_at",
            attributes={
                "record_kind": "derived_event_candidate",
                "event_type": "relationship_start",
                "event_date": event_date,
                "aliases": ["第一次在一起", "确定关系", "恋爱第一天", "在一起纪念日"],
                "evidence_memory_id": evidence.memory_id,
                "date_basis": "explicit" if explicit_date else "source_timestamp",
            },
            extractor_version=EVENT_EXTRACTOR_VERSION,
            human_review={"status": "pending"},
        ))
    candidates.sort(key=lambda card: (-card.confidence, card.created_at, card.memory_id))
    return candidates


__all__ = [
    "EVIDENCE_EXTRACTOR_VERSION",
    "EVENT_EXTRACTOR_VERSION",
    "build_source_evidence_cards",
    "extract_relationship_event_candidates",
]
