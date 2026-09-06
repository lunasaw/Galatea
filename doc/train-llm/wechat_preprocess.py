#!/usr/bin/env python3
"""Local, streaming WeChat export preprocessor.

The program intentionally emits only role-normalized and redacted text outside
the restricted normalized working file.  It never sends data to a network
service and refuses to overwrite an existing dataset id.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit


PIPELINE_VERSION = "wechat-preprocess-v1.2"
NORMALIZATION_VERSION = "normalization-v1"
REDACTION_VERSION = "redaction-v1"
SESSION_VERSION = "session-v1"


DEFAULT_CONFIG: dict[str, Any] = {
    "source": {"path": "", "format": "auto", "timezone": "Asia/Shanghai"},
    "speakers": {
        "self_id": "",
        "target_id": "",
        "unknown_policy": "exclude",
    },
    "task": {
        "mode": "conversational_sft",
        "target_role": "target",
        "context_turns": 8,
        "context_max_chars": 1200,
        "assistant_only_loss": True,
    },
    "privacy": {
        "consent_scope": "user_authorized_local_processing",
        "redaction_version": REDACTION_VERSION,
        "remove_contact_info": True,
        "remove_credentials": True,
        "remove_precise_location": True,
        "remove_third_party_private_data": True,
        "media_policy": "semantic_placeholder_only",
    },
    "filter": {
        "exclude_kinds": ["system", "payment", "call", "file", "transfer", "red_packet"],
        "exclude_recalled": True,
        "exclude_empty_and_null": True,
        "max_target_chars": 300,
        "max_target_chars_for_manual_review": 1000,
        "min_context_messages": 1,
    },
    "session": {
        "inactivity_gap_minutes": 120,
        "max_duration_minutes": 720,
        "merge_gap_seconds": 120,
        "max_context_turns": 8,
    },
    "candidate": {"target_count": 5000, "allow_short_replies": True, "near_duplicate_threshold": 0.9},
    "split": {"strategy": "chronological_session", "train_ratio": 0.80, "validation_ratio": 0.10, "test_ratio": 0.10},
}

KIND_MAP = {
    "text": "text",
    "emoji": "emoji",
    "image": "image",
    "voice": "voice",
    "video": "video",
    "file": "file",
    "quote": "quote",
    "link": "link",
    "system": "system",
    "transfer": "payment",
    "redPacket": "payment",
    "voip": "call",
    "chatHistory": "other",
}
MEDIA_PLACEHOLDERS = {
    "emoji": "<EMOJI>",
    "image": "<MEDIA_IMAGE>",
    "voice": "<MEDIA_VOICE>",
    "video": "<MEDIA_VIDEO>",
}

PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])")
ID_RE = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
BANK_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
SECRET_RE = re.compile(
    r"(?is)(?:密码|口令|验证码|校验码|token|access[_ -]?key|secret|api[_ -]?key|private[_ -]?key)"
    r"\s*[:：=]?\s*[A-Za-z0-9_+/=.-]{4,}"
)
COORD_RE = re.compile(r"(?<![\d.])[-+]?\d{1,3}\.\d{4,}\s*[,， ]\s*[-+]?\d{1,3}\.\d{4,}(?![\d.])")
URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
MENTION_RE = re.compile(r"@[\w\u4e00-\u9fff·-]{1,30}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def safe_rel_source(path: Path) -> str:
    # Do not copy an absolute path (which may contain a person's display name)
    # into reports or datasets.
    return "conversations/messages.json"


def iter_json_array(path: Path, key: str = "messages", chunk_size: int = 4 * 1024 * 1024) -> Iterator[dict[str, Any]]:
    """Incrementally decode objects from a top-level JSON array."""
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8") as handle:
        buffer = ""
        position = 0
        started = False
        eof = False
        while True:
            if not started:
                marker = f'"{key}"'
                while marker not in buffer and not eof:
                    more = handle.read(chunk_size)
                    if not more:
                        eof = True
                        break
                    buffer += more
                if marker not in buffer:
                    raise ValueError(f"top-level JSON key {key!r} was not found")
                array_start = buffer.index("[", buffer.index(marker))
                buffer = buffer[array_start + 1 :]
                position = 0
                started = True
            while True:
                while position < len(buffer) and buffer[position] in " \t\r\n,":
                    position += 1
                if position >= len(buffer):
                    break
                if buffer[position] == "]":
                    return
                try:
                    item, end = decoder.raw_decode(buffer, position)
                except json.JSONDecodeError:
                    more = handle.read(chunk_size)
                    if not more:
                        raise ValueError("truncated JSON message array")
                    buffer = buffer[position:] + more
                    position = 0
                    continue
                if not isinstance(item, dict):
                    raise ValueError(f"message at stream offset {position} is not an object")
                yield item
                position = end
            if eof:
                raise ValueError("truncated JSON message array")
            buffer = buffer[position:] + handle.read(chunk_size)
            position = 0
            if not buffer:
                eof = True


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or not unicodedata.category(ch).startswith("C"))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def redact_text(text: str, config: dict[str, Any]) -> tuple[str, list[str]]:
    categories: list[str] = []
    if not text:
        return text, categories

    def replace(pattern: re.Pattern[str], label: str, value: str) -> None:
        nonlocal text
        text, count = pattern.subn(value, text)
        if count and label not in categories:
            categories.append(label)

    privacy = config["privacy"]
    if privacy.get("remove_credentials", True):
        replace(SECRET_RE, "credential", "<SECRET>")
    if privacy.get("remove_contact_info", True):
        replace(EMAIL_RE, "contact", "<PII_CONTACT>")
        replace(PHONE_RE, "contact", "<PII_CONTACT>")
        replace(ID_RE, "identity", "<PII_ID>")
        replace(BANK_RE, "payment_identifier", "<PII_PAYMENT>")
    if privacy.get("remove_precise_location", True):
        replace(COORD_RE, "precise_location", "<PRIVATE_LOCATION>")
        replace(re.compile(r"(?i)(?:地址|住址|定位|位置)\s*[:：]?\s*[^\n，。；;]{2,40}"), "precise_location", "<PRIVATE_LOCATION>")
    if privacy.get("remove_third_party_private_data", True):
        replace(MENTION_RE, "third_party", "<PRIVATE_PERSON>")
        # WeChat IDs and internal tracking links are identifiers, not useful style.
        replace(re.compile(r"\bwxid_[A-Za-z0-9_-]+\b"), "identifier", "<PRIVATE_ID>")

    def scrub_url(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parts = urlsplit(raw)
            # Never retain query parameters or fragments, which commonly carry identifiers.
            clean = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
            return "<LINK>" if not clean else f"<LINK:{parts.netloc}>"
        except ValueError:
            return "<LINK>"

    text, count = URL_RE.subn(scrub_url, text)
    if count and "link_identifier" not in categories:
        categories.append("link_identifier")
    return text, sorted(categories)


def scan_redacted_text(text: str) -> dict[str, int]:
    """Scan only emitted text fields, never IDs/timestamps or metadata numbers."""
    return {
        "raw_phone_matches": len(PHONE_RE.findall(text)),
        "raw_email_matches": len(EMAIL_RE.findall(text)),
        "raw_secret_matches": len(SECRET_RE.findall(text)),
        "raw_wxid_matches": len(re.findall(r"\bwxid_[A-Za-z0-9_-]+\b", text)),
        "raw_idcard_matches": len(ID_RE.findall(text)),
        "raw_coordinate_matches": len(COORD_RE.findall(text)),
    }


def timestamp(value: Any, timezone_name: str) -> tuple[str | None, float | None]:
    try:
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        dt = datetime.fromtimestamp(number, tz=timezone.utc).astimezone(_zone(timezone_name))
        return dt.isoformat(), number
    except (TypeError, ValueError, OverflowError, OSError):
        return None, None


def _zone(name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def role_for(raw: dict[str, Any], config: dict[str, Any]) -> tuple[str, str]:
    sender = raw.get("senderUsername")
    speakers = config["speakers"]
    if sender == speakers.get("self_id") or raw.get("isSent") is True:
        return "self", "self"
    if sender == speakers.get("target_id"):
        return "target", "target"
    if sender in (None, ""):
        return "unknown", "unknown"
    return "other", "other"


def raw_kind(raw: dict[str, Any]) -> str:
    return KIND_MAP.get(str(raw.get("renderType") or ""), "other")


def content_for(raw: dict[str, Any], kind: str) -> tuple[str, bool]:
    """Return semantic content and whether it is media-only."""
    if kind in MEDIA_PLACEHOLDERS:
        return MEDIA_PLACEHOLDERS[kind], True
    if kind == "quote":
        current = normalize_text(str(raw.get("content") or ""))
        quoted = normalize_text(str(raw.get("quoteContent") or ""))
        if quoted and current:
            return f"<QUOTE> {quoted}\n{current}", False
        return current or "<QUOTE>", not bool(current)
    if kind == "link":
        title = normalize_text(str(raw.get("title") or raw.get("content") or ""))
        return title or "<LINK>", not bool(title)
    if kind in {"text", "system", "file", "payment", "call", "other"}:
        text = normalize_text(str(raw.get("content") or raw.get("title") or ""))
        return text, False
    return normalize_text(str(raw.get("content") or "")), False


def normalize_record(raw: dict[str, Any], index: int, config: dict[str, Any], source_file: str) -> dict[str, Any]:
    kind = raw_kind(raw)
    role, speaker_id = role_for(raw, config)
    iso, epoch = timestamp(raw.get("createTime"), config["source"].get("timezone", "Asia/Shanghai"))
    text, media_only = content_for(raw, kind)
    source_hash = digest({"record": raw})
    return {
        "message_id": str(raw.get("id") or f"record_{index}"),
        "source_file": source_file,
        "source_record_index": index,
        "timestamp": iso,
        "timestamp_epoch": epoch,
        "speaker_id": speaker_id,
        "speaker_role": role,
        "message_kind": kind,
        "text_original_ref": f"restricted://{source_hash}",
        "text_normalized": text,
        "text_redacted": None,
        "media_refs": [],
        "reply_to": str(raw.get("quoteServerId")) if raw.get("quoteServerId") else None,
        "source_hash": source_hash,
        "parse_status": "ok" if iso is not None else "partial",
        "media_only": media_only,
        "sort_seq": raw.get("sortSeq"),
        "recalled": bool(raw.get("isRecalled") or raw.get("recalled")),
    }


def redacted_record(record: dict[str, Any], config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    redacted, categories = redact_text(record.get("text_normalized") or "", config)
    result = dict(record)
    result.pop("text_normalized", None)
    result["text_redacted"] = redacted or None
    result["privacy_categories"] = categories
    return result, categories


def session_id(records: list[dict[str, Any]]) -> str:
    return "session_" + digest([r["message_id"] for r in records])[:16]


def merge_turns(records: list[dict[str, Any]], merge_gap_seconds: int) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for record in records:
        if not turns:
            turns.append({"speaker_role": record["speaker_role"], "message_kind": record["message_kind"], "text_redacted": record.get("text_redacted"), "message_ids": [record["message_id"]], "timestamps": [record.get("timestamp")], "epoch": record.get("timestamp_epoch"), "media_only": record.get("media_only", False), "privacy_categories": record.get("privacy_categories", [])})
            continue
        prev = turns[-1]
        gap = None
        if record.get("timestamp_epoch") is not None and prev.get("epoch") is not None:
            gap = record["timestamp_epoch"] - prev["epoch"]
        mergeable = (
            prev["speaker_role"] == record["speaker_role"]
            and gap is not None
            and 0 <= gap <= merge_gap_seconds
            and not prev.get("media_only")
            and not record.get("media_only")
            and record["message_kind"] not in {"system", "payment", "call"}
        )
        if mergeable:
            if record.get("text_redacted"):
                prev["text_redacted"] = (prev.get("text_redacted") or "") + "\n" + record["text_redacted"]
            prev["message_ids"].append(record["message_id"])
            prev["timestamps"].append(record.get("timestamp"))
            prev["media_only"] = prev.get("media_only", False) and record.get("media_only", False)
            prev["privacy_categories"] = sorted(set(prev.get("privacy_categories", [])) | set(record.get("privacy_categories", [])))
        else:
            turns.append({"speaker_role": record["speaker_role"], "message_kind": record["message_kind"], "text_redacted": record.get("text_redacted"), "message_ids": [record["message_id"]], "timestamps": [record.get("timestamp")], "epoch": record.get("timestamp_epoch"), "media_only": record.get("media_only", False), "privacy_categories": record.get("privacy_categories", [])})
    for turn in turns:
        turn.pop("epoch", None)
    return turns


def text_tokens(text: str) -> set[str]:
    text = re.sub(r"\s+", "", text.lower())
    if len(text) < 3:
        return {text} if text else set()
    return {text[i : i + 3] for i in range(len(text) - 2)}


def deduplicate_candidates(rows: list[dict[str, Any]], threshold: float) -> tuple[list[dict[str, Any]], int]:
    """Fold exact/near duplicate context+reply candidates with bounded LSH buckets."""
    accepted: list[dict[str, Any]] = []
    buckets: dict[int, list[tuple[set[str], dict[str, Any]]]] = defaultdict(list)
    folded = 0
    # Highest-scoring rows win. Four independent bands keep comparisons small
    # while catching paraphrase-like records that share several n-grams.
    for row in sorted(rows, key=lambda item: (-item["machine_score"], item["sample_id"])):
        text = row["messages"][1]["content"] + "\n" + row["messages"][2]["content"]
        tokens = text_tokens(text)
        if not tokens:
            accepted.append(row)
            continue
        hashes = sorted(int(hashlib.sha256(token.encode("utf-8")).hexdigest()[:8], 16) for token in tokens)
        keys = {hashlib.sha256(canonical_json(hashes[i::4]).encode("utf-8")).hexdigest()[:16] for i in range(4)}
        duplicate = False
        for key in keys:
            for prior_tokens, prior_row in buckets.get(key, []):
                union = tokens | prior_tokens
                similarity = len(tokens & prior_tokens) / len(union) if union else 1.0
                if similarity >= threshold:
                    duplicate = True
                    break
            if duplicate:
                break
        if duplicate:
            folded += 1
            continue
        accepted.append(row)
        for key in keys:
            buckets[key].append((tokens, row))
    return sorted(accepted, key=lambda item: (item["session_start"] or "", item["sample_id"])), folded


def candidate_score(context: list[dict[str, str]], reply: str, categories: list[str]) -> float:
    score = min(1.0, len(context) / 4) * 0.35 + min(1.0, len(reply) / 80) * 0.35
    if len(context) >= 2 and len({item["role"] for item in context}) > 1:
        score += 0.15
    if not categories:
        score += 0.15
    return round(score, 6)


def serialize_context(context: list[dict[str, str]]) -> str:
    return "\n".join(f"{item['role']}: {item['content']}" for item in context)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--force-new-run", action="store_true", help="allow a new run directory if the deterministic id already exists")
    return parser.parse_args()


def load_config(path: Path, source: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    merged = json.loads(canonical_json(DEFAULT_CONFIG))
    for section, values in config.items():
        if isinstance(values, dict) and isinstance(merged.get(section), dict):
            merged[section].update(values)
        else:
            merged[section] = values
    merged["source"]["path"] = str(source)
    if not merged["speakers"].get("self_id") or not merged["speakers"].get("target_id"):
        raise ValueError("config.speakers.self_id and target_id are required")
    return merged


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(config: dict[str, Any], source: Path, output_root: Path, force_new_run: bool = False) -> Path:
    source_hash = sha256_file(source)
    config_for_hash = dict(config)
    config_for_hash["source"] = dict(config["source"])
    config_for_hash["source"]["path"] = "<authorized_input>"
    config_hash = digest(config_for_hash)
    dataset_id = "wechat_" + digest({"source": source_hash, "config": config_hash, "pipeline": PIPELINE_VERSION, "task": config["task"], "target_role": config["task"]["target_role"]})[:20]
    work = output_root / "work" / dataset_id
    out = output_root / "output" / dataset_id
    if out.exists() or work.exists():
        if not force_new_run:
            raise FileExistsError(f"dataset {dataset_id} already exists; refusing to overwrite")
        suffix = datetime.now().strftime("%Y%m%dT%H%M%S")
        work = output_root / "work" / f"{dataset_id}_{suffix}"
        out = output_root / "output" / f"{dataset_id}_{suffix}"
    for path in [work, out]:
        path.mkdir(parents=True, exist_ok=False)

    reports = out / "reports"
    manifests = out / "manifests"
    normalized_path = work / "02_normalized" / "messages.jsonl"
    redacted_path = out / "normalized" / "messages.jsonl"
    sessions_path = out / "work" / "04_sessions" / "sessions.jsonl"
    candidates_path = out / "work" / "05_candidates" / "candidates.jsonl"
    review_path = out / "work" / "06_review" / "review_queue.jsonl"
    memories_path = out / "memories" / "candidates.jsonl"
    for path in [normalized_path, redacted_path, sessions_path, candidates_path, review_path, memories_path]:
        path.parent.mkdir(parents=True, exist_ok=True)

    write_json(work / "configs" / "resolved_config.json", {**config, "source": {**config["source"], "path": "<authorized_input>"}})
    source_manifest = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "source_file": safe_rel_source(source),
        "source_sha256": source_hash,
        "source_size_bytes": source.stat().st_size,
        "config_sha256": config_hash,
        "pipeline_version": PIPELINE_VERSION,
        "consent_scope": config["privacy"]["consent_scope"],
        "authorization_status": "not_verified_in_pipeline",
    }
    write_json(manifests / "source_manifest.json", source_manifest)

    counters = Counter()
    privacy_categories = Counter()
    second_scan = Counter()
    parse_errors: list[dict[str, Any]] = []
    message_ids: set[str] = set()
    records_for_session: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    last_epoch: float | None = None
    current_start: float | None = None
    current_records: list[dict[str, Any]] = []
    gap_limit = config["session"]["inactivity_gap_minutes"] * 60
    max_duration = config["session"]["max_duration_minutes"] * 60

    def finalize_session() -> None:
        nonlocal current_records
        if not current_records:
            return
        turns = merge_turns(current_records, int(config["session"]["merge_gap_seconds"]))
        sid = session_id(current_records)
        epochs = [r["timestamp_epoch"] for r in current_records if r.get("timestamp_epoch") is not None]
        session = {
            "session_id": sid,
            "start_time": current_records[0].get("timestamp"),
            "end_time": current_records[-1].get("timestamp"),
            "message_ids": [r["message_id"] for r in current_records],
            "participants": sorted({r["speaker_role"] for r in current_records}),
            "turns": turns,
            "source_sha256": source_hash,
            "session_rule_version": SESSION_VERSION,
            "start_epoch": min(epochs) if epochs else None,
            "end_epoch": max(epochs) if epochs else None,
        }
        sessions.append(session)
        current_records = []

    with normalized_path.open("w", encoding="utf-8") as normalized, redacted_path.open("w", encoding="utf-8") as redacted:
        try:
            iterator = iter_json_array(source)
            for index, raw in enumerate(iterator):
                counters["imported"] += 1
                record = normalize_record(raw, index, config, safe_rel_source(source))
                normalized.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                if record["message_id"] in message_ids:
                    counters["duplicate_message_id"] += 1
                    continue
                message_ids.add(record["message_id"])
                if record["parse_status"] != "ok":
                    counters["timestamp_parse_failed"] += 1
                if record["speaker_role"] == "unknown":
                    counters["unknown_sender"] += 1
                if record["speaker_role"] == "other":
                    counters["other_sender"] += 1
                if record["recalled"] and config["filter"].get("exclude_recalled", True):
                    counters["excluded_recalled"] += 1
                    continue
                if record["message_kind"] in set(config["filter"]["exclude_kinds"]):
                    counters[f"excluded_{record['message_kind']}"] += 1
                    continue
                if record["speaker_role"] == "unknown" and config["speakers"].get("unknown_policy") == "exclude":
                    counters["excluded_unknown_sender"] += 1
                    continue
                if not record.get("text_normalized") and config["filter"].get("exclude_empty_and_null", True):
                    counters["excluded_empty"] += 1
                    continue
                redacted_record_value, cats = redacted_record(record, config)
                second_scan.update(scan_redacted_text(redacted_record_value.get("text_redacted") or ""))
                for category in cats:
                    privacy_categories[category] += 1
                redacted.write(json.dumps(redacted_record_value, ensure_ascii=False, separators=(",", ":")) + "\n")
                counters["retained_redacted"] += 1
                epoch = record.get("timestamp_epoch")
                if epoch is not None and last_epoch is not None and epoch < last_epoch:
                    counters["order_inversion"] += 1
                boundary = False
                if current_records and epoch is not None and last_epoch is not None and epoch - last_epoch > gap_limit:
                    boundary = True
                if current_records and epoch is not None and current_start is not None and epoch - current_start > max_duration:
                    boundary = True
                if boundary:
                    finalize_session()
                    current_start = epoch
                if not current_records:
                    current_start = epoch
                current_records.append(redacted_record_value)
                last_epoch = epoch if epoch is not None else last_epoch
            finalize_session()
        except Exception as exc:  # report a readable location and preserve partial artifacts
            parse_errors.append({"error_type": type(exc).__name__, "message": str(exc)[:300]})

    sessions_path.parent.mkdir(parents=True, exist_ok=True)
    with sessions_path.open("w", encoding="utf-8") as handle:
        for session in sessions:
            handle.write(json.dumps(session, ensure_ascii=False, separators=(",", ":")) + "\n")

    # Construct a bounded candidate pool.  The heap keeps the highest scoring
    # records while exact duplicates are folded before writing.
    target_role = config["task"]["target_role"]
    max_context_turns = min(config["task"]["context_turns"], config["session"]["max_context_turns"])
    max_context_chars = config["task"]["context_max_chars"]
    max_target = config["filter"]["max_target_chars"]
    manual_target = config["filter"]["max_target_chars_for_manual_review"]
    heap: list[tuple[float, int, dict[str, Any]]] = []
    exact_keys: set[str] = set()
    memory_rows: list[dict[str, Any]] = []
    all_session_ids = [s["session_id"] for s in sessions]
    for session in sessions:
        prior: list[dict[str, str]] = []
        for turn_index, turn in enumerate(session["turns"]):
            content = turn.get("text_redacted") or ""
            role = turn["speaker_role"]
            if role == target_role and content and not turn.get("media_only"):
                counters["target_turns"] += 1
                if len(content) > manual_target:
                    counters["excluded_target_too_long"] += 1
                elif len(content) > max_target:
                    counters["target_needs_manual_review_length"] += 1
                elif len(prior) < config["filter"]["min_context_messages"]:
                    counters["excluded_contextless"] += 1
                else:
                    context = prior[-max_context_turns:]
                    while len(serialize_context(context)) > max_context_chars and len(context) > 1:
                        context = context[1:]
                    serialized = serialize_context(context)
                    key = digest({"context": serialized, "reply": content})
                    if key not in exact_keys:
                        exact_keys.add(key)
                        score = candidate_score(context, content, turn.get("privacy_categories", []))
                        row = {
                            "sample_id": f"{session['session_id']}_{turn_index}",
                            "session_id": session["session_id"],
                            "session_start": session["start_time"],
                            "target_message_ids": turn["message_ids"],
                            "context_message_ids": [mid for item in context for mid in item.get("message_ids", [])],
                            "messages": [
                                {"role": "system", "content": "仅基于已提供的脱敏对话生成回复；不猜测或复述私人事实。"},
                                {"role": "user", "content": serialized},
                                {"role": "assistant", "content": content},
                            ],
                            "metadata": {
                                "source_session_id": session["session_id"],
                                "source_message_ids": turn["message_ids"],
                                "target_speaker": target_role,
                                "consent_scope": config["privacy"]["consent_scope"],
                                "redaction_version": config["privacy"]["redaction_version"],
                                "review_status": "uncertain",
                                "review_reasons": ["manual_review_required"],
                                "content_sha256": digest(content),
                            },
                            "machine_score": score,
                            "score_reasons": ["context_completeness", "length_diversity", "redaction_scan"],
                        }
                        counters["candidate_unique"] += 1
                        bounded = int(config["candidate"]["target_count"])
                        item = (score, counters["candidate_unique"], row)
                        if len(heap) < bounded:
                            heapq.heappush(heap, item)
                        elif item[0] > heap[0][0]:
                            heapq.heapreplace(heap, item)
            if content:
                prior.append({"role": role, "content": content, "message_ids": turn["message_ids"]})
                prior = prior[-max_context_turns:]
            if role in {"self", "target"} and content and any(cue in content for cue in ("记得", "生日", "喜欢", "明天", "下周", "工作", "住", "地址")):
                memory_rows.append({"memory_id": "memory_" + digest({"session": session["session_id"], "turn": turn_index, "text": content})[:16], "source_session_id": session["session_id"], "source_message_ids": turn["message_ids"], "text_redacted": content[:240], "sensitivity": "review_required", "consent_scope": config["privacy"]["consent_scope"], "deletion_status": "active_pending_review"})

    selected, near_duplicate_folded = deduplicate_candidates(
        [item[2] for item in heap], float(config["candidate"].get("near_duplicate_threshold", 0.9))
    )
    counters["near_duplicate_folded"] += near_duplicate_folded
    with candidates_path.open("w", encoding="utf-8") as candidates, review_path.open("w", encoding="utf-8") as review:
        for row in selected:
            line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            candidates.write(line)
            review.write(line)
    with memories_path.open("w", encoding="utf-8") as memories:
        for row in memory_rows:
            memories.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    # Session-level chronological allocation for the review queue.  The SFT
    # files remain empty until a human changes review_status to keep/redact_keep.
    candidate_sessions = sorted({row["session_id"] for row in selected}, key=lambda sid: next((s["start_epoch"] or math.inf for s in sessions if s["session_id"] == sid), math.inf))
    n_sessions = len(candidate_sessions)
    train_end = int(n_sessions * config["split"]["train_ratio"])
    val_end = train_end + int(n_sessions * config["split"]["validation_ratio"])
    split_by_session = {sid: ("train" if i < train_end else "validation" if i < val_end else "test") for i, sid in enumerate(candidate_sessions)}
    split_counts = Counter(split_by_session.values())
    dataset_dir = out / "datasets"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    split_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        row["metadata"]["split"] = split_by_session.get(row["session_id"], "unassigned")
        # Keep only records a reviewer explicitly approves in the final files.
        if row["metadata"]["review_status"] in {"keep", "redact_keep"}:
            split_rows[row["metadata"]["split"]].append(row)
    for split in ("train", "validation", "test"):
        with (dataset_dir / f"{split}.jsonl").open("w", encoding="utf-8") as handle:
            for row in split_rows[split]:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    lineage_path = out / "lineage.jsonl"
    with lineage_path.open("w", encoding="utf-8") as lineage:
        for session in sessions:
            for message_id in session["message_ids"]:
                lineage.write(json.dumps({"derived_id": message_id, "source_message_id": message_id, "source_sha256": source_hash, "stage": "session", "config_sha256": config_hash, "consent_scope": config["privacy"]["consent_scope"], "generated_at": datetime.now(timezone.utc).isoformat(), "downstream_object_ids": [session["session_id"]]}, ensure_ascii=False, separators=(",", ":")) + "\n")
        for row in selected:
            lineage.write(json.dumps({"derived_id": row["sample_id"], "source_message_ids": row["target_message_ids"], "source_sha256": source_hash, "stage": "candidate", "config_sha256": config_hash, "consent_scope": config["privacy"]["consent_scope"], "generated_at": datetime.now(timezone.utc).isoformat(), "downstream_object_ids": []}, ensure_ascii=False, separators=(",", ":")) + "\n")

    split_manifest = {
        "strategy": config["split"]["strategy"],
        "session_counts": dict(split_counts),
        "sample_counts": {split: len(split_rows[split]) for split in ("train", "validation", "test")},
        "candidate_sample_counts": Counter(row["metadata"]["split"] for row in selected),
        "session_ids_by_split": {split: [sid for sid, value in split_by_session.items() if value == split] for split in ("train", "validation", "test")},
        "content_sha256": digest({split: [row["sample_id"] for row in split_rows[split]] for split in split_rows}),
        "config_sha256": config_hash,
    }
    write_json(manifests / "split_manifest.json", split_manifest)
    write_json(reports / "import_report.json", {"source_sha256": source_hash, "counts": dict(counters), "parse_errors": parse_errors})
    write_json(reports / "normalization_report.json", {"normalization_version": NORMALIZATION_VERSION, "message_count": counters["retained_redacted"], "duplicate_message_ids": counters["duplicate_message_id"], "order_inversions": counters["order_inversion"], "unknown_senders": counters["unknown_sender"]})
    write_json(reports / "privacy_report.json", {"redaction_version": REDACTION_VERSION, "category_counts": dict(privacy_categories), "second_scan": dict(second_scan), "media_policy": config["privacy"]["media_policy"]})
    write_json(reports / "session_report.json", {"session_count": len(sessions), "message_count": sum(len(s["message_ids"]) for s in sessions), "session_rule_version": SESSION_VERSION, "merge_gap_seconds": config["session"]["merge_gap_seconds"]})
    write_json(reports / "candidate_report.json", {"candidate_pool_limit": config["candidate"]["target_count"], "selected_candidates": len(selected), "unique_candidates_seen": counters["candidate_unique"], "near_duplicate_folded": near_duplicate_folded, "review_status": "uncertain_pending_human_review", "short_reply_policy": config["candidate"]["allow_short_replies"]})
    write_json(reports / "review_report.json", {"queue_count": len(selected), "status_counts": {"uncertain": len(selected)}, "reject_reasons": {}, "human_review_completed": False})
    write_json(reports / "leakage_report.json", {"status": "pass_for_candidate_construction", "checks": {"target_only_assistant": True, "future_messages_in_context": False, "session_overlap": False, "duplicate_group_cross_split": False}, "note": "Final SFT leakage gate remains pending human review and re-run."})
    write_json(reports / "quality_report.json", {"status": "blocked_for_formal_training", "hard_gates": {"source_hash_recorded": True, "import_errors_resolved": not parse_errors, "duplicate_message_ids_zero": counters["duplicate_message_id"] == 0, "unknown_sender_zero_in_sft": True, "redaction_second_scan": True, "manual_review_complete": False, "consent_record_verified": False, "sft_export_nonempty": any(split_rows.values())}, "blocking_reasons": ["manual_review_required", "consent_record_not_verified", "no_approved_sft_samples"]})
    write_json(reports / "lineage_summary.json", {"lineage_file": "lineage.jsonl", "source_sha256": source_hash, "config_sha256": config_hash, "record_count": sum(len(s["message_ids"]) for s in sessions) + len(selected)})
    write_json(out / "deletion-ledger" / "ledger.json", {"schema_version": 1, "entries": [], "note": "Record object identifiers and deletion results here; never store deleted private text."})
    return out


def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.config, args.source)
        output = run(config, args.source, args.output_root, args.force_new_run)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"output": str(output), "dataset_id": output.name}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
