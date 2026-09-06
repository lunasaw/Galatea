"""Privacy-aware style and memory dataset preparation helpers.

These helpers are deliberately pure and deterministic so a governed Driver
can hash their output and record the exact preprocessing version.  They do not
write source text, and all project-specific data export remains outside the
source tree.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .memory import MemoryRecord, build_grounded_messages, make_memory_id, utc_now


PREPROCESSING_VERSION = "style-memory-preprocess-v1"
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d -]{7,}\d)(?!\d)")
_DATE = re.compile(r"(?<!\d)(?:20\d{2}[-年/.]\d{1,2}[-月/.]\d{1,2}日?)(?!\d)")
_URL = re.compile(r"https?://[^\s]+")
_ACCOUNT = re.compile(r"(?i)(?:账号|account|uid|id)\s*[:：]?\s*[A-Za-z0-9_-]{4,}")


def redact_style_text(text: str, *, extra_entities: Mapping[str, str] | None = None) -> str:
    """Replace direct identifiers while retaining sentence shape and semantics."""

    value = str(text)
    value = _URL.sub("<链接>", value)
    value = _EMAIL.sub("<邮箱>", value)
    value = _DATE.sub("<日期>", value)
    value = _PHONE.sub("<电话>", value)
    value = _ACCOUNT.sub("<账号>", value)
    for source, placeholder in sorted((extra_entities or {}).items(), key=lambda item: len(item[0]), reverse=True):
        if source:
            value = value.replace(source, placeholder)
    return value


def normalize_messages(messages: Sequence[Mapping[str, Any]], *, extra_entities: Mapping[str, str] | None = None) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role", ""))
        content = redact_style_text(str(message.get("content", "")), extra_entities=extra_entities)
        if role not in {"system", "user", "assistant"} or not content.strip():
            continue
        normalized.append({"role": role, "content": content.strip()})
    return normalized


def canonical_sample_key(messages: Sequence[Mapping[str, str]]) -> str:
    payload = "\n".join(f"{item['role']}:{item['content']}" for item in messages)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _token_shingles(text: str, width: int = 3) -> set[tuple[str, ...]]:
    tokens = re.findall(r"\w+|[^\w\s]", text.casefold(), re.UNICODE)
    return {tuple(tokens[index : index + width]) for index in range(max(0, len(tokens) - width + 1))}


def near_duplicate(a: str, b: str, threshold: float = 0.9) -> bool:
    left, right = _token_shingles(a), _token_shingles(b)
    if not left or not right:
        return a.strip() == b.strip()
    return len(left & right) / len(left | right) >= threshold


def deduplicate_style_samples(rows: Iterable[Mapping[str, Any]], *, threshold: float = 0.9) -> list[dict[str, Any]]:
    """Fold exact and near duplicate conversations in deterministic order."""

    selected: list[dict[str, Any]] = []
    exact: set[str] = set()
    fingerprints: list[str] = []
    for row in rows:
        messages = normalize_messages(row.get("messages", []), extra_entities=row.get("entity_placeholders"))
        if len(messages) < 2 or messages[-1]["role"] != "assistant":
            continue
        key = canonical_sample_key(messages)
        if key in exact:
            continue
        assistant = messages[-1]["content"]
        if any(near_duplicate(assistant, prior, threshold) for prior in fingerprints):
            continue
        exact.add(key)
        fingerprints.append(assistant)
        copy = dict(row)
        copy["messages"] = messages
        copy.setdefault("metadata", {})
        copy["metadata"] = {
            **dict(copy["metadata"]),
            "preprocessing_version": PREPROCESSING_VERSION,
            "content_sha256": hashlib.sha256(assistant.encode("utf-8")).hexdigest(),
        }
        selected.append(copy)
    return selected


def build_style_samples(rows: Iterable[Mapping[str, Any]], *, max_reply_chars: int = 800, near_duplicate_threshold: float = 0.9) -> list[dict[str, Any]]:
    """Build assistant-only-loss style samples from multi-turn conversations."""

    result: list[dict[str, Any]] = []
    for row in rows:
        messages = normalize_messages(row.get("messages", []), extra_entities=row.get("entity_placeholders"))
        if not messages or messages[-1]["role"] != "assistant" or len(messages[-1]["content"]) > max_reply_chars:
            continue
        result.append({
            "sample_id": str(row.get("sample_id") or hashlib.sha256(canonical_sample_key(messages).encode()).hexdigest()[:20]),
            "scenario_id": str(row.get("session_id") or row.get("scenario_id") or "session"),
            "messages": messages,
            "metadata": {
                **dict(row.get("metadata") or {}),
                "task": "style_reply",
                "style_label": str((row.get("metadata") or {}).get("style_label", "chat_style")),
                "preprocessing_version": PREPROCESSING_VERSION,
                "assistant_only_loss": True,
            },
        })
    return deduplicate_style_samples(result, threshold=near_duplicate_threshold)


def build_memory_records(
    rows: Iterable[Mapping[str, Any]],
    *,
    owner_scope: str,
    confirmed_only: bool = False,
    now: str | None = None,
) -> list[MemoryRecord]:
    """Convert explicitly extracted user facts into removable memory records.

    Model-generated responses are ignored by requiring ``speaker_role`` (when
    present) to be ``user``/``self``.  ``confirmed_only`` is useful for
    serving, while candidate records can be exported for human review.
    """

    current_time = now or utc_now()
    records: list[MemoryRecord] = []
    for row in rows:
        speaker = str(row.get("speaker_role", row.get("role", "user")))
        if speaker not in {"user", "self"}:
            continue
        content = redact_style_text(str(row.get("content", row.get("text_redacted", ""))))
        if not content.strip():
            continue
        status = str(row.get("status", "candidate" if not confirmed_only else "confirmed"))
        if confirmed_only and status != "confirmed":
            continue
        source_ids = tuple(str(item) for item in row.get("source_message_ids", row.get("message_ids", [])))
        record = MemoryRecord(
            memory_id=str(row.get("memory_id") or make_memory_id(owner_scope, content, source_ids)),
            owner_scope=owner_scope,
            content=content,
            source_message_ids=source_ids,
            created_at=str(row.get("created_at", current_time)),
            valid_from=row.get("valid_from"),
            valid_to=row.get("valid_to"),
            confidence=float(row.get("confidence", 0.5 if status == "candidate" else 1.0)),
            status=status,
            sensitivity=str(row.get("sensitivity", "sensitive")),
            attributes=dict(row.get("attributes") or {}),
        )
        records.append(record)
    return records


def build_memory_grounded_sample(
    *,
    sample_id: str,
    owner_scope: str,
    question: str,
    answer: str,
    records: Sequence[MemoryRecord],
    scenario_id: str,
) -> dict[str, Any]:
    """Create a memory SFT sample whose evidence is visible in the input."""

    from .memory import RetrievedMemory

    retrieved = [RetrievedMemory(record, 1.0, index + 1) for index, record in enumerate(records)]
    return {
        "sample_id": sample_id,
        "scenario_id": scenario_id,
        "messages": build_grounded_messages(question, retrieved),
        "metadata": {
            "task": "memory_grounded_reply",
            "owner_scope": owner_scope,
            "style_label": "grounded_brief",
            "preprocessing_version": PREPROCESSING_VERSION,
            "assistant_only_loss": True,
            "seed": 42,
            "evidence_memory_ids": [record.memory_id for record in records],
            "expected_answer": answer,
        },
    }
