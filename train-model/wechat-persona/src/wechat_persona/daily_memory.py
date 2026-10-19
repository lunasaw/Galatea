"""Deterministic daily bundles, chunking, date resolution, and fact ledgers."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
import re
from typing import Any, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from ._common import canonical_json, digest, parse_datetime
from .redact import scan_atomic_text


DAILY_BUNDLE_VERSION = "daily-bundle-v1"
FACT_EXTRACTION_VERSION = "fact-extraction-v1"
FACT_LEDGER_VERSION = "fact-ledger-v1"
FACT_RESOLVER_VERSION = "wechat-fact-resolver-v1"
ALIAS_REGISTRY_VERSION = "wechat-fact-aliases-v1"
DATE_PARSER_VERSION = "wechat-date-parser-v1"
RELATIVE_MARKERS = ("今天", "昨天", "前天", "明天", "后天", "那天", "第二天", "当天")
FACT_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
OWNER_SCOPE_RE = re.compile(r"^owner_[a-f0-9]{24}$")
ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日|号)?(?!\d)")
MONTH_DAY_RE = re.compile(r"(?<!\d)(\d{1,2})[-/.月](\d{1,2})(?:日|号)?(?!\d)")
YEAR_MONTH_RE = re.compile(r"(?<!\d)(\d{4})[-/.年](\d{1,2})(?:月)?(?!\d)")
YEAR_RE = re.compile(r"(?<!\d)(\d{4})(?:年)(?!\d)")
MONTH_RE = re.compile(r"(?<!\d)(\d{1,2})月(?!\d)")

RELATIONSHIP_STARTED_ALIASES = (
    "表白",
    "告白",
    "示爱",
    "在一起",
    "确定关系",
    "开始交往",
    "正式交往",
    "恋爱第一天",
    "关系开始",
    "纪念日",
)


class DailyMemoryError(RuntimeError):
    """Raised when daily preprocessing cannot preserve its contract."""


@dataclass(frozen=True)
class ChunkPolicy:
    target_input_tokens: int
    hard_input_tokens: int
    overlap_turns: int

    def __post_init__(self) -> None:
        if self.target_input_tokens < 256:
            raise DailyMemoryError("target_input_tokens must be at least 256")
        if self.hard_input_tokens < self.target_input_tokens:
            raise DailyMemoryError("hard_input_tokens must be >= target_input_tokens")
        if self.overlap_turns < 0:
            raise DailyMemoryError("overlap_turns must be non-negative")


@dataclass(frozen=True)
class DateResolution:
    normalized_value: str
    precision: str
    basis: str
    unresolved: tuple[str, ...] = ()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _local_datetime(value: Any, timezone_name: str) -> datetime:
    parsed = parse_datetime(value, timezone_name)
    if parsed is None:
        raise DailyMemoryError("session contains an invalid timestamp")
    try:
        return parsed.astimezone(ZoneInfo(timezone_name))
    except Exception as exc:  # pragma: no cover - ZoneInfo errors vary by host
        raise DailyMemoryError(f"invalid business timezone: {timezone_name}") from exc


def _evidence_ref(source_manifest_sha256: str, message_id: str) -> str:
    return "e_" + digest([source_manifest_sha256, message_id])[:20]


def _bundle_digest(payload: Mapping[str, Any]) -> str:
    return digest({key: value for key, value in payload.items() if key != "bundle_digest"})


def build_daily_bundles(
    sessions: Iterable[Mapping[str, Any]],
    *,
    owner_scope: str,
    source_manifest_sha256: str,
    timezone_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Build day buckets while retaining session, turn, and atomic-message boundaries."""

    if not OWNER_SCOPE_RE.fullmatch(owner_scope):
        raise DailyMemoryError("owner_scope must be an opaque owner_<24 hex> identifier")
    if not re.fullmatch(r"[a-f0-9]{64}", source_manifest_sha256):
        raise DailyMemoryError("source_manifest_sha256 must be a SHA-256 digest")
    ZoneInfo(timezone_name)
    day_sessions: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    session_bounds: dict[tuple[str, str], list[str]] = {}
    lineage: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    seen_message_ids: set[str] = set()
    seen_evidence_refs: set[str] = set()

    ordered_sessions = sorted(
        (dict(row) for row in sessions),
        key=lambda row: (str(row.get("start_time") or ""), str(row.get("session_id") or "")),
    )
    for session in ordered_sessions:
        session_id = str(session.get("session_id") or "")
        turns = session.get("turns")
        if not session_id or not isinstance(turns, list):
            raise DailyMemoryError("session_id and turns are required")
        counts["session_count"] += 1
        for turn_ordinal, turn_value in enumerate(turns):
            if not isinstance(turn_value, Mapping):
                raise DailyMemoryError(f"session {session_id} contains a non-object turn")
            turn = dict(turn_value)
            if str(turn.get("message_kind") or "text") != "text":
                counts["excluded_non_text_turn_count"] += 1
                counts["excluded_non_text_message_count"] += len(turn.get("message_ids") or [])
                continue
            role = str(turn.get("speaker_role") or "unknown")
            if role not in {"self", "target", "other", "unknown"}:
                raise DailyMemoryError(f"session {session_id} contains invalid speaker_role")
            message_ids = turn.get("message_ids")
            timestamps = turn.get("timestamps")
            contents = turn.get("message_contents")
            if not all(isinstance(value, list) for value in (message_ids, timestamps, contents)):
                raise DailyMemoryError(
                    f"session {session_id} lacks atomic message boundaries"
                )
            if not (len(message_ids) == len(timestamps) == len(contents)):
                raise DailyMemoryError(
                    f"session {session_id} has mismatched atomic message fields"
                )
            by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for message_ordinal, (message_id_value, timestamp_value, text_value) in enumerate(
                zip(message_ids, timestamps, contents, strict=True)
            ):
                message_id = str(message_id_value or "")
                text = str(text_value or "").strip()
                if not message_id:
                    raise DailyMemoryError(f"session {session_id} contains an empty message_id")
                if message_id in seen_message_ids:
                    raise DailyMemoryError(f"duplicate message_id in sessions: {message_id}")
                seen_message_ids.add(message_id)
                if not text:
                    counts["empty_message_count"] += 1
                    continue
                local_time = _local_datetime(timestamp_value, timezone_name)
                day_id = local_time.date().isoformat()
                evidence_ref = _evidence_ref(source_manifest_sha256, message_id)
                if evidence_ref in seen_evidence_refs:
                    raise DailyMemoryError("evidence_ref collision")
                seen_evidence_refs.add(evidence_ref)
                privacy = scan_atomic_text(text)
                if privacy["hard_leak_count"]:
                    raise DailyMemoryError(
                        f"privacy scanner rejected evidence_ref {evidence_ref}"
                    )
                message = {
                    "evidence_ref": evidence_ref,
                    "message_id_digest": _sha256_text(message_id),
                    "timestamp": local_time.isoformat(),
                    "text": text,
                }
                by_day[day_id].append(message)
                lineage.append(
                    {
                        "schema_version": "daily-memory-lineage-v1",
                        "evidence_ref": evidence_ref,
                        "message_id": message_id,
                        "message_id_digest": message["message_id_digest"],
                        "session_id": session_id,
                        "conversation_date": day_id,
                        "turn_ordinal": turn_ordinal,
                        "message_ordinal": message_ordinal,
                    }
                )
                counts["message_count"] += 1
                counts["input_privacy_hard_leak_count"] += privacy["hard_leak_count"]
            for day_id, messages in sorted(by_day.items()):
                day_sessions[day_id][session_id].append(
                    {"speaker_role": role, "messages": messages}
                )
                bounds = session_bounds.setdefault((day_id, session_id), [])
                bounds.extend(message["timestamp"] for message in messages)

    bundles: list[dict[str, Any]] = []
    for day_id, sessions_for_day in sorted(day_sessions.items()):
        bundled_sessions: list[dict[str, Any]] = []
        for session_id, turns in sorted(
            sessions_for_day.items(),
            key=lambda item: session_bounds[(day_id, item[0])][0],
        ):
            bounds = session_bounds[(day_id, session_id)]
            bundled_sessions.append(
                {
                    "session_id": session_id,
                    "start_time": min(bounds),
                    "end_time": max(bounds),
                    "turns": turns,
                }
            )
        bundle: dict[str, Any] = {
            "schema_version": DAILY_BUNDLE_VERSION,
            "day_id": day_id,
            "timezone": timezone_name,
            "owner_scope": owner_scope,
            "source_manifest_sha256": source_manifest_sha256,
            "sessions": bundled_sessions,
        }
        bundle["bundle_digest"] = _bundle_digest(bundle)
        bundles.append(bundle)
    counts["day_count"] = len(bundles)
    counts["session_day_slice_count"] = sum(
        len(bundle["sessions"]) for bundle in bundles
    )
    lineage.sort(key=lambda row: (row["conversation_date"], row["session_id"], row["turn_ordinal"], row["message_ordinal"]))
    return bundles, lineage, dict(sorted(counts.items()))


def public_turn(turn: Mapping[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    payload = {
        "speaker_role": str(turn["speaker_role"]),
        "messages": [
            {
                "evidence_ref": str(message["evidence_ref"]),
                "timestamp": str(message["timestamp"]),
                "text": str(message["text"]),
            }
            for message in turn["messages"]
        ],
    }
    if session_id is not None:
        payload["session_id"] = session_id
    return payload


def request_input(
    *,
    day_id: str,
    session_id: str,
    primary_turns: Sequence[Mapping[str, Any]],
    context_turns: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    return {
        "conversation_date": day_id,
        "session_id": session_id,
        "primary_turns": [public_turn(turn) for turn in primary_turns],
        "neighbor_context": [dict(turn) for turn in context_turns],
    }


def chunk_daily_bundles(
    bundles: Sequence[Mapping[str, Any]],
    *,
    source_manifest_sha256: str,
    policy: ChunkPolicy,
    count_request_tokens: Callable[[Mapping[str, Any]], int],
) -> list[dict[str, Any]]:
    """Chunk primary evidence without splitting a session or a complete turn."""

    chunks: list[dict[str, Any]] = []
    for bundle in bundles:
        day_id = str(bundle["day_id"])
        for session in bundle["sessions"]:
            session_id = str(session["session_id"])
            turns = list(session["turns"])
            empty_request = request_input(
                day_id=day_id,
                session_id=session_id,
                primary_turns=[],
            )
            base_tokens = count_request_tokens(empty_request)
            weighted_turns = [
                (
                    dict(turn),
                    max(
                        1,
                        count_request_tokens(
                            request_input(
                                day_id=day_id,
                                session_id=session_id,
                                primary_turns=[turn],
                            )
                        )
                        - base_tokens
                        + 2,
                    ),
                )
                for turn in turns
            ]
            current: list[tuple[dict[str, Any], int]] = []
            current_tokens = base_tokens
            current_overlap_count = 0
            ordinal = 0

            def emit(values: Sequence[tuple[Mapping[str, Any], int]], overlap_count: int) -> None:
                nonlocal ordinal
                if not values:
                    return
                primary = [dict(value) for value, _ in values]
                request = request_input(
                    day_id=day_id,
                    session_id=session_id,
                    primary_turns=primary,
                )
                input_tokens = count_request_tokens(request)
                evidence_refs = [
                    str(message["evidence_ref"])
                    for turn in primary
                    for message in turn["messages"]
                ]
                key_payload = {
                    "source_manifest_sha256": source_manifest_sha256,
                    "day_id": day_id,
                    "session_id": session_id,
                    "chunk_ordinal": ordinal,
                    "evidence_refs": evidence_refs,
                    "input_digest": digest(request),
                }
                chunks.append(
                    {
                        "chunk_id": "chunk_" + digest(key_payload)[:20],
                        "day_id": day_id,
                        "session_id": session_id,
                        "chunk_ordinal": ordinal,
                        "primary_turns": primary,
                        "primary_evidence_refs": evidence_refs,
                        "overlap_turn_count": overlap_count,
                        "input_digest": key_payload["input_digest"],
                        "input_tokens_without_context": input_tokens,
                        "blocked_reason": (
                            "primary_chunk_exceeds_hard_input_tokens"
                            if input_tokens > policy.hard_input_tokens
                            else None
                        ),
                    }
                )
                ordinal += 1

            for turn, turn_tokens in weighted_turns:
                if current and current_tokens + turn_tokens > policy.target_input_tokens:
                    emit(current, current_overlap_count)
                    overlap = current[-policy.overlap_turns :] if policy.overlap_turns else []
                    current = list(overlap)
                    current_tokens = base_tokens + sum(value[1] for value in current)
                    while current and current_tokens + turn_tokens > policy.target_input_tokens:
                        current_tokens -= current[0][1]
                        current.pop(0)
                    current_overlap_count = len(current)
                    current.append((turn, turn_tokens))
                    current_tokens += turn_tokens
                else:
                    current.append((turn, turn_tokens))
                    current_tokens += turn_tokens
            emit(current, current_overlap_count)
    return chunks


def _turn_has_relative_marker(turn: Mapping[str, Any]) -> bool:
    return any(
        marker in str(message.get("text") or "")
        for message in turn.get("messages") or []
        for marker in RELATIVE_MARKERS
    )


def has_relative_marker(chunk: Mapping[str, Any]) -> bool:
    return any(_turn_has_relative_marker(turn) for turn in chunk["primary_turns"])


def neighboring_context(
    bundles_by_day: Mapping[str, Mapping[str, Any]],
    chunk: Mapping[str, Any],
    *,
    days: int,
    turns_per_day: int,
) -> tuple[list[dict[str, Any]], int]:
    """Return a small, explicitly labeled context window from neighboring days."""

    if days <= 0 or turns_per_day <= 0 or not has_relative_marker(chunk):
        return [], 0
    anchor = date.fromisoformat(str(chunk["day_id"]))
    selected: list[dict[str, Any]] = []
    available = 0
    for offset in range(-days, days + 1):
        if offset == 0:
            continue
        day_id = (anchor + timedelta(days=offset)).isoformat()
        bundle = bundles_by_day.get(day_id)
        if not bundle:
            continue
        turns = [
            public_turn(turn, session_id=str(session["session_id"]))
            for session in bundle["sessions"]
            for turn in session["turns"]
        ]
        available += len(turns)
        chosen = turns[-turns_per_day:] if offset < 0 else turns[:turns_per_day]
        for turn in chosen:
            turn["conversation_date"] = day_id
        selected.extend(chosen)
    selected.sort(
        key=lambda turn: (
            str(turn.get("conversation_date") or ""),
            str((turn.get("messages") or [{}])[0].get("timestamp") or ""),
        )
    )
    return selected, available


def fit_neighboring_context(
    chunk: Mapping[str, Any],
    context_turns: Sequence[Mapping[str, Any]],
    *,
    hard_input_tokens: int,
    count_request_tokens: Callable[[Mapping[str, Any]], int],
) -> tuple[list[dict[str, Any]], int]:
    """Bound optional context explicitly while never truncating primary evidence."""

    included: list[dict[str, Any]] = []
    skipped = 0
    anchor = date.fromisoformat(str(chunk["day_id"]))
    nearest_first = sorted(
        context_turns,
        key=lambda turn: (
            abs((date.fromisoformat(str(turn["conversation_date"])) - anchor).days),
            str(turn["conversation_date"]),
            str((turn.get("messages") or [{}])[0].get("timestamp") or ""),
        ),
    )
    for turn in nearest_first:
        candidate = [*included, dict(turn)]
        tokens = count_request_tokens(
            request_input(
                day_id=str(chunk["day_id"]),
                session_id=str(chunk["session_id"]),
                primary_turns=chunk["primary_turns"],
                context_turns=candidate,
            )
        )
        if tokens <= hard_input_tokens:
            included = candidate
        else:
            skipped += 1
    included.sort(
        key=lambda turn: (
            str(turn["conversation_date"]),
            str((turn.get("messages") or [{}])[0].get("timestamp") or ""),
        )
    )
    return included, skipped


def _valid_date(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def resolve_date(value: str, *, anchor_timestamp: str, declared_basis: str) -> DateResolution:
    """Normalize explicit and simple relative dates without inventing a missing year."""

    text = re.sub(r"\s+", "", str(value))
    anchor = datetime.fromisoformat(anchor_timestamp.replace("Z", "+00:00")).date()
    relative_offsets = {"前天": -2, "昨天": -1, "今天": 0, "当天": 0, "明天": 1, "后天": 2}
    for marker, offset in relative_offsets.items():
        if marker in text:
            return DateResolution(
                (anchor + timedelta(days=offset)).isoformat(),
                "day",
                "relative_resolved",
            )
    match = ISO_DATE_RE.search(text)
    if match:
        normalized = _valid_date(*(int(part) for part in match.groups()))
        if normalized:
            basis = declared_basis if declared_basis in {"explicit", "relative_resolved"} else "explicit"
            return DateResolution(normalized, "day", basis)
    match = YEAR_MONTH_RE.search(text)
    if match:
        year, month = (int(part) for part in match.groups())
        if 1 <= month <= 12:
            return DateResolution(f"{year:04d}-{month:02d}", "month", "explicit")
    match = MONTH_DAY_RE.search(text)
    if match:
        month, day = (int(part) for part in match.groups())
        if _valid_date(2000, month, day):
            return DateResolution(
                f"--{month:02d}-{day:02d}",
                "unknown",
                "unknown",
                ("year",),
            )
    match = YEAR_RE.search(text)
    if match:
        return DateResolution(match.group(1), "year", "explicit")
    match = MONTH_RE.search(text)
    if match and 1 <= int(match.group(1)) <= 12:
        return DateResolution(
            f"--{int(match.group(1)):02d}",
            "unknown",
            "unknown",
            ("year",),
        )
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        normalized = _valid_date(*(int(part) for part in text.split("-")))
        if normalized:
            return DateResolution(normalized, "day", declared_basis)
    return DateResolution(text, "unknown", "unknown", ("date",))


def normalize_fact_candidate(
    raw: Mapping[str, Any],
    *,
    chunk: Mapping[str, Any],
    evidence_lookup: Mapping[str, Mapping[str, Any]],
    extractor_version: str,
    allow_inference: bool = False,
) -> dict[str, Any]:
    """Validate evidence binding and enrich one model result as a candidate."""

    fact_key = str(raw.get("fact_key") or "")
    if not FACT_KEY_RE.fullmatch(fact_key):
        raise DailyMemoryError(f"invalid fact_key from extractor: {fact_key!r}")
    evidence_rows = raw.get("evidence")
    if not isinstance(evidence_rows, list) or not evidence_rows:
        raise DailyMemoryError("every fact requires evidence")
    primary_refs = set(str(value) for value in chunk["primary_evidence_refs"])
    validated_evidence: list[dict[str, str]] = []
    for item in evidence_rows:
        if not isinstance(item, Mapping):
            raise DailyMemoryError("fact evidence must be an object")
        ref = str(item.get("evidence_ref") or "")
        quote = str(item.get("quote") or "")
        source = evidence_lookup.get(ref)
        if source is None:
            raise DailyMemoryError(f"fact references unknown evidence_ref {ref}")
        if not quote or len(quote) > 240 or quote not in str(source["text"]):
            raise DailyMemoryError(f"fact quote is not an exact source substring for {ref}")
        if scan_atomic_text(quote)["hard_leak_count"]:
            raise DailyMemoryError(f"fact quote failed privacy scan for {ref}")
        validated_evidence.append({"evidence_ref": ref, "quote": quote})
    if not primary_refs.intersection(item["evidence_ref"] for item in validated_evidence):
        raise DailyMemoryError("fact is supported only by neighbor context")
    unique_evidence = {
        (item["evidence_ref"], item["quote"]): item for item in validated_evidence
    }
    validated_evidence = [unique_evidence[key] for key in sorted(unique_evidence)]
    anchor = evidence_lookup[validated_evidence[0]["evidence_ref"]]
    value_type = str(raw.get("value_type") or "")
    value = raw.get("value")
    date_precision = str(raw.get("date_precision") or "unknown")
    date_basis = str(raw.get("date_basis") or "unknown")
    unresolved = [str(item) for item in (raw.get("unresolved_references") or []) if str(item)]
    if value_type == "date":
        if not isinstance(value, str):
            raise DailyMemoryError("date fact value must be a string")
        resolution = resolve_date(
            value,
            anchor_timestamp=str(anchor["timestamp"]),
            declared_basis=date_basis,
        )
        normalized_value: Any = resolution.normalized_value
        date_precision = resolution.precision
        date_basis = resolution.basis
        unresolved.extend(resolution.unresolved)
    elif value_type == "string":
        if not isinstance(value, str) or not value.strip():
            raise DailyMemoryError("string fact value must be non-empty")
        normalized_value = re.sub(r"\s+", " ", value).strip()
        date_precision = "unknown"
        date_basis = "unknown"
    elif value_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DailyMemoryError("number fact value must be numeric")
        normalized_value = value
        date_precision = "unknown"
        date_basis = "unknown"
    elif value_type == "boolean":
        if not isinstance(value, bool):
            raise DailyMemoryError("boolean fact value must be boolean")
        normalized_value = value
        date_precision = "unknown"
        date_basis = "unknown"
    else:
        raise DailyMemoryError(f"invalid value_type from extractor: {value_type!r}")
    aliases = {str(item).strip() for item in raw.get("aliases") or [] if str(item).strip()}
    if fact_key == "relationship.started_at":
        aliases.update(RELATIONSHIP_STARTED_ALIASES)
    claim_type = str(raw.get("claim_type") or "")
    polarity = str(raw.get("polarity") or "")
    confidence = raw.get("confidence")
    if claim_type not in {"explicit", "relative", "quoted", "hypothetical", "inferred"}:
        raise DailyMemoryError("invalid claim_type from extractor")
    if claim_type == "inferred" and not allow_inference:
        raise DailyMemoryError("inferred fact rejected by extraction policy")
    if polarity not in {"positive", "negative", "uncertain"}:
        raise DailyMemoryError("invalid polarity from extractor")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise DailyMemoryError("invalid confidence from extractor")
    id_payload = {
        "day_id": chunk["day_id"],
        "session_id": chunk["session_id"],
        "fact_key": fact_key,
        "normalized_value": normalized_value,
        "evidence": validated_evidence,
    }
    extraction_id = "extract_" + digest(id_payload)[:20]
    lineage_payload = {
        **id_payload,
        "chunk_id": chunk["chunk_id"],
        "extractor_version": extractor_version,
    }
    return {
        "schema_version": FACT_EXTRACTION_VERSION,
        "extraction_id": extraction_id,
        "day_id": str(chunk["day_id"]),
        "session_id": str(chunk["session_id"]),
        "chunk_ordinal": int(chunk["chunk_ordinal"]),
        "fact_key": fact_key,
        "value": value,
        "normalized_value": normalized_value,
        "value_type": value_type,
        "date_precision": date_precision,
        "date_basis": date_basis,
        "claim_type": claim_type,
        "polarity": polarity,
        "confidence": float(confidence),
        "aliases": sorted(aliases),
        "evidence": validated_evidence,
        "unresolved_references": sorted(set(unresolved)),
        "status": "candidate",
        "extractor_version": extractor_version,
        "lineage_digest": digest(lineage_payload),
    }


def deduplicate_candidates(candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate overlap results without discarding conflicting values."""

    by_key: dict[tuple[str, str, str, tuple[str, ...]], dict[str, Any]] = {}
    for value in candidates:
        row = dict(value)
        refs = tuple(sorted(str(item["evidence_ref"]) for item in row["evidence"]))
        key = (
            str(row["day_id"]),
            str(row["fact_key"]),
            canonical_json(row["normalized_value"]),
            refs,
        )
        existing = by_key.get(key)
        if existing is None or float(row["confidence"]) > float(existing["confidence"]):
            by_key[key] = row
    return sorted(
        by_key.values(),
        key=lambda row: (row["day_id"], row["fact_key"], canonical_json(row["normalized_value"]), row["extraction_id"]),
    )


def build_fact_ledger(
    candidates: Iterable[Mapping[str, Any]], *, owner_scope: str
) -> list[dict[str, Any]]:
    """Group candidates across days while preserving disagreement for review."""

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for value in candidates:
        row = dict(value)
        if row.get("status") != "candidate":
            raise DailyMemoryError("fact ledger accepts candidate records only")
        groups[str(row["fact_key"])].append(row)
    ledger: list[dict[str, Any]] = []
    for fact_key, facts in sorted(groups.items()):
        by_value: dict[str, list[dict[str, Any]]] = defaultdict(list)
        aliases: set[str] = set()
        for fact in facts:
            by_value[canonical_json(fact["normalized_value"])].append(fact)
            aliases.update(str(alias) for alias in fact.get("aliases") or [])
        candidate_groups: list[dict[str, Any]] = []
        reliable_values = 0
        for value_key, matching in sorted(by_value.items()):
            evidence_refs = sorted(
                {
                    str(item["evidence_ref"])
                    for fact in matching
                    for item in fact["evidence"]
                }
            )
            claim_types = sorted({str(fact["claim_type"]) for fact in matching})
            polarities = sorted({str(fact["polarity"]) for fact in matching})
            if (
                "positive" in polarities
                and any(value in {"explicit", "relative"} for value in claim_types)
                and all(not fact.get("unresolved_references") for fact in matching)
            ):
                reliable_values += 1
            precisions = sorted({str(fact["date_precision"]) for fact in matching})
            candidate_groups.append(
                {
                    "normalized_value": json.loads(value_key),
                    "precision": precisions[0] if len(precisions) == 1 else "unknown",
                    "evidence_refs": evidence_refs,
                    "source_days": sorted({str(fact["day_id"]) for fact in matching}),
                    "extraction_ids": sorted({str(fact["extraction_id"]) for fact in matching}),
                    "claim_types": claim_types,
                    "polarities": polarities,
                    "support_count": len(evidence_refs),
                }
            )
        conflict_status = (
            "conflicted" if reliable_values > 1 else "none" if reliable_values == 1 else "insufficient"
        )
        fact_group_id = "factgrp_" + digest([owner_scope, fact_key])[:20]
        lineage_payload = {
            "owner_scope": owner_scope,
            "fact_key": fact_key,
            "extraction_ids": sorted(str(fact["extraction_id"]) for fact in facts),
        }
        ledger.append(
            {
                "schema_version": FACT_LEDGER_VERSION,
                "fact_group_id": fact_group_id,
                "owner_scope": owner_scope,
                "fact_key": fact_key,
                "aliases": sorted(aliases),
                "candidates": candidate_groups,
                "conflict_status": conflict_status,
                "review_status": "pending",
                "confirmed_value": None,
                "lineage_digest": digest(lineage_payload),
            }
        )
    return ledger


def evidence_lookup_from_bundles(
    bundles: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for bundle in bundles:
        for session in bundle["sessions"]:
            for turn in session["turns"]:
                for message in turn["messages"]:
                    ref = str(message["evidence_ref"])
                    lookup[ref] = {
                        "text": str(message["text"]),
                        "timestamp": str(message["timestamp"]),
                        "day_id": str(bundle["day_id"]),
                        "session_id": str(session["session_id"]),
                    }
    return lookup


__all__ = [
    "ALIAS_REGISTRY_VERSION",
    "ChunkPolicy",
    "DAILY_BUNDLE_VERSION",
    "DATE_PARSER_VERSION",
    "DailyMemoryError",
    "FACT_EXTRACTION_VERSION",
    "FACT_LEDGER_VERSION",
    "FACT_RESOLVER_VERSION",
    "RELATIONSHIP_STARTED_ALIASES",
    "build_daily_bundles",
    "build_fact_ledger",
    "chunk_daily_bundles",
    "deduplicate_candidates",
    "evidence_lookup_from_bundles",
    "fit_neighboring_context",
    "has_relative_marker",
    "neighboring_context",
    "normalize_fact_candidate",
    "request_input",
    "resolve_date",
]
