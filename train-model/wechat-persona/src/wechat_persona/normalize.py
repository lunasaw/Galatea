"""Convert importer records to the versioned message schema."""
from __future__ import annotations

from typing import Any, Mapping

from ._common import digest, iso_datetime
from .redact import REDACTION_VERSION, redact_text

NORMALIZATION_VERSION = "wechat-normalization-v1"
KIND_MAP = {"text": "text", "emoji": "emoji", "image": "image", "voice": "voice", "video": "video", "file": "file", "quote": "quote", "link": "link", "system": "system", "payment": "payment", "call": "call"}
ROLE_VALUES = {"self", "target", "other", "unknown"}


def _role(raw: Mapping[str, Any], speaker_map: Mapping[str, str] | None) -> str:
    value = raw.get("speaker_role", raw.get("role", raw.get("speaker")))
    if speaker_map and value in speaker_map:
        value = speaker_map[value]
    value = str(value or "unknown").casefold()
    aliases = {"me": "self", "user": "self", "assistant": "target", "friend": "target"}
    value = aliases.get(value, value)
    return value if value in ROLE_VALUES else "unknown"


def normalize_message(
    raw: Mapping[str, Any], *, index: int | None = None, timezone_name: str = "UTC", speaker_map: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    idx = int(raw.get("source_record_index", 0 if index is None else index))
    timestamp = iso_datetime(raw.get("timestamp", raw.get("time", raw.get("createTime"))), timezone_name)
    text = raw.get("text", raw.get("content", raw.get("title", "")))
    kind_raw = str(raw.get("message_kind", raw.get("kind", raw.get("type", "text")))).casefold()
    kind = KIND_MAP.get(kind_raw, "other")
    if kind in {"emoji", "image", "voice", "video"} and not str(text or "").strip():
        text = {"emoji": "<EMOJI>", "image": "<MEDIA_IMAGE>", "voice": "<MEDIA_VOICE>", "video": "<MEDIA_VIDEO>"}[kind]
    redacted = redact_text(text)
    message_id = str(raw.get("message_id", raw.get("id", f"record_{idx}")))
    return {
        "message_id": message_id,
        "source_record_index": idx,
        "timestamp": timestamp,
        "speaker_role": _role(raw, speaker_map),
        "message_kind": kind,
        "text_redacted": redacted.text or None,
        "source_ref": None,
        "media_refs": [str(item) for item in (raw.get("media_refs") or [])],
        "reply_to": str(raw["reply_to"]) if raw.get("reply_to") is not None else None,
        "source_hash": str(raw.get("source_hash") or digest(dict(raw))),
        "parse_status": "ok" if timestamp else "partial",
        "redaction_categories": list(redacted.categories),
        "preprocessing_version": f"{NORMALIZATION_VERSION}+{REDACTION_VERSION}",
    }
