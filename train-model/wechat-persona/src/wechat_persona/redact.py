"""Deterministic privacy redaction and a second-pass hard-leak scanner.

The scanner intentionally operates on *content fields*, never on a stringified
row.  Stringifying a structured candidate would scan role labels, ids and
timestamps and, more importantly, would miss credentials split over adjacent
turns.  ``scan_atomic_text`` is the primitive used by the layer-aware helpers
below; ``scan_redacted_text`` remains as a compatibility alias for callers
that only have one text value.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit


REDACTION_VERSION = "wechat-redaction-v2"
PRIVACY_SCANNER_VERSION = "wechat-privacy-scanner-v3"
# A short alias is useful in manifests and keeps the name discoverable for
# clients that used ``SCANNER_VERSION`` in pre-release integrations.
SCANNER_VERSION = PRIVACY_SCANNER_VERSION
PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])")
ID_RE = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
BANK_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
# Keep the label expression shared by inline and adjacent-turn checks.  English
# labels use boundaries so words such as ``tokenizer`` do not become secrets;
# Chinese labels intentionally have no word boundary.
_SECRET_LABEL = r"(?:密码|口令|验证码|校验码|verification[ _-]?code|one[ _-]?time[ _-]?password|password|passcode|passwd|otp|pin|token|access[ _-]?key|secret|api[ _-]?key|private[ _-]?key)"
SECRET_RE = re.compile(
    rf"(?is)(?<![A-Za-z0-9_]){_SECRET_LABEL}(?![A-Za-z0-9_])"
    # Deliberately use horizontal whitespace only.  Sessionization joins
    # same-speaker messages with a newline; allowing ``\s`` here would turn a
    # label in one source message plus an unrelated word in the next message
    # into an inline credential before the boundary-aware scanner can inspect
    # the two original messages.
    rf"[ \t]*[:：=]?[ \t]*[A-Za-z0-9_+/=.-]{{4,}}"
)
SECRET_LABEL_RE = re.compile(rf"(?i)(?<![A-Za-z0-9_]){_SECRET_LABEL}(?![A-Za-z0-9_])")
COORD_RE = re.compile(r"(?<![\d.])[-+]?\d{1,3}\.\d{4,}\s*[,， ]\s*[-+]?\d{1,3}\.\d{4,}(?![\d.])")
IPV4_RE = re.compile(
    r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])"
)
URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
WXID_RE = re.compile(r"\bwxid_[A-Za-z0-9_-]+\b")
ADDRESS_RE = re.compile(
    r"(?i)(?:地址|住址)\s*[:：]\s*[^\n，。；;]{2,40}"
    r"|(?:定位|位置)\s*[:：]\s*(?:[^\n，。；;]{2,40})"
)
# Do not treat the ``@domain`` part of an email as a third-party mention.
MENTION_RE = re.compile(r"(?<![\w.+-])@[\w\u4e00-\u9fff·-]{1,30}")

_REDACTION_PLACEHOLDERS = {
    "<SECRET>",
    "<PII_CONTACT>",
    "<PII_ID>",
    "<PII_PAYMENT>",
    "<PRIVATE_LOCATION>",
    "<PRIVATE_ID>",
    "<PRIVATE_PERSON>",
    "<LINK>",
}
_ROLE_WORDS = {"self", "target", "other", "unknown", "me", "user", "assistant", "friend"}
_SECRET_VALUE_PATTERNS = {
    "code": re.compile(
        r"\d{4,12}|(?=[A-Za-z0-9_+/=]{4,}$)(?=.*\d)[A-Za-z0-9_+/=]+"
    ),
    "password": re.compile(r"[A-Za-z0-9_+/=]{4,}"),
}


@dataclass(frozen=True)
class RedactionResult:
    text: str
    categories: tuple[str, ...]

    def __str__(self) -> str:
        return self.text


def normalize_text(text: Any) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(ch for ch in value if ch in "\n\t" or not unicodedata.category(ch).startswith("C"))
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def redact_text(text: Any, *, extra_entities: dict[str, str] | None = None) -> RedactionResult:
    value = normalize_text(text)
    categories: set[str] = set()

    def replace(pattern: re.Pattern[str], label: str, replacement: str) -> None:
        nonlocal value
        value, count = pattern.subn(replacement, value)
        if count:
            categories.add(label)

    replace(SECRET_RE, "credential", "<SECRET>")
    replace(EMAIL_RE, "contact", "<PII_CONTACT>")
    replace(PHONE_RE, "contact", "<PII_CONTACT>")
    replace(ID_RE, "identity", "<PII_ID>")
    replace(BANK_RE, "payment_identifier", "<PII_PAYMENT>")
    replace(COORD_RE, "precise_location", "<PRIVATE_LOCATION>")
    replace(IPV4_RE, "network_identifier", "<PRIVATE_ID>")
    replace(ADDRESS_RE, "precise_location", "<PRIVATE_LOCATION>")
    replace(WXID_RE, "identifier", "<PRIVATE_ID>")
    replace(MENTION_RE, "third_party", "<PRIVATE_PERSON>")

    def clean_url(match: re.Match[str]) -> str:
        try:
            parts = urlsplit(match.group(0))
            host = parts.netloc.split("@")[-1].split(":")[0]
            return f"<LINK:{host}>" if host else "<LINK>"
        except ValueError:
            return "<LINK>"

    value, count = URL_RE.subn(clean_url, value)
    if count:
        categories.add("link_identifier")
    for source, replacement in sorted((extra_entities or {}).items(), key=lambda item: len(item[0]), reverse=True):
        if source:
            value = value.replace(source, replacement)
            categories.add("custom_entity")
    return RedactionResult(value, tuple(sorted(categories)))


def _is_placeholder(value: str) -> bool:
    """Return whether ``value`` is one of our safe redaction markers."""

    stripped = value.strip()
    if stripped in _REDACTION_PLACEHOLDERS:
        return True
    # URL redaction preserves a host in ``<LINK:example.invalid>``.  It is
    # still a marker, not a raw URL or an address that needs to be blocked.
    return stripped.startswith("<LINK:") and stripped.endswith(">")


def _mask_spans(value: str, pattern: re.Pattern[str]) -> str:
    """Mask matches before a second pattern is applied.

    Identity numbers also satisfy the broad payment-number expression.  Masking
    them avoids reporting one value as two independent hard leaks while keeping
    the detailed category counts useful.
    """

    return pattern.sub(lambda match: " " * len(match.group(0)), value)


def _address_matches(value: str) -> int:
    count = 0
    for match in ADDRESS_RE.finditer(value):
        captured = match.group(0)
        # A redacted ``地址: <PRIVATE_LOCATION>`` must not fail its own scan.
        if not any(marker in captured for marker in _REDACTION_PLACEHOLDERS) and "<LINK:" not in captured:
            count += 1
    return count


def scan_atomic_text(text: Any) -> dict[str, int]:
    """Scan one content value without returning the matched text.

    The returned mapping is deliberately numeric and stable so it can be put in
    privacy reports.  It is safe to call on ``None`` and on non-string values;
    callers should nevertheless pass only user-visible content fields.
    """

    value = normalize_text(text)
    # Do not let a broad payment expression double-count a 18-digit identity
    # number.  The identity category remains authoritative for that value.
    id_masked = _mask_spans(value, ID_RE)
    counts = {
        "raw_phone_matches": len(PHONE_RE.findall(value)),
        "raw_email_matches": len(EMAIL_RE.findall(value)),
        "raw_secret_matches": len(SECRET_RE.findall(value)),
        "raw_wxid_matches": len(WXID_RE.findall(value)),
        "raw_idcard_matches": len(ID_RE.findall(value)),
        "raw_coordinate_matches": len(COORD_RE.findall(value)),
        "raw_ipv4_matches": len(IPV4_RE.findall(value)),
        "raw_bank_matches": len(BANK_RE.findall(id_masked)),
        "raw_address_matches": _address_matches(value),
        "raw_url_matches": len(URL_RE.findall(value)),
        "raw_mention_matches": len(MENTION_RE.findall(value)),
    }
    counts["hard_leak_count"] = sum(counts.values())
    return counts


def _content_value(message: Any) -> str:
    """Extract content from a structured message, excluding role/metadata."""

    if isinstance(message, Mapping):
        for key in ("content", "text_redacted", "text"):
            if key in message and message.get(key) is not None:
                return str(message.get(key) or "")
        return ""
    return str(message or "")


def _looks_like_secret_value(value: str, *, allow_dotted: bool = False) -> bool:
    candidate = value.strip().strip("`'\"“”‘’，。；;:：,!?！？()[]{}")
    if not candidate or _is_placeholder(candidate) or candidate.casefold() in _ROLE_WORDS:
        return False
    # A split credential is normally a compact token or numeric code.  Requiring
    # an exact token prevents a role label (for example ``self``) from becoming
    # a false positive while still catching ``abc123`` and ``123456``.
    token_pattern = r"[A-Za-z0-9_+/=.-]{4,}" if allow_dotted else r"[A-Za-z0-9_+/=]{4,}"
    return bool(re.fullmatch(token_pattern, candidate))


def _split_secret_value(previous: str, current: str) -> str | None:
    """Return the compact value when adjacent messages form a credential."""

    if not previous or not current or SECRET_RE.search(previous):
        return None
    label_match = SECRET_LABEL_RE.search(previous)
    if not label_match:
        return None
    label_text = label_match.group(0)
    stripped_previous = previous.strip()
    short_label_phrase = (
        label_match.end() == len(stripped_previous)
        and len(stripped_previous) <= 8
    )
    candidate = current.strip().strip("`'\"“”‘’，。；;:：,!?！？()[]{}")
    if label_text in {"验证码", "校验码"}:
        qualifies = short_label_phrase and bool(
            _SECRET_VALUE_PATTERNS["code"].fullmatch(candidate)
        )
    elif label_text in {"密码", "口令"}:
        qualifies = (
            short_label_phrase
            and candidate.casefold() not in _ROLE_WORDS
            and bool(_SECRET_VALUE_PATTERNS["password"].fullmatch(candidate))
        )
    else:
        qualifies = stripped_previous == label_text and _looks_like_secret_value(current)
    return candidate if qualifies else None


def _adjacent_secret_matches(messages: Iterable[Any]) -> int:
    values = list(messages)
    matches = 0
    for previous_row, current_row in zip(values, values[1:]):
        previous = _content_value(previous_row)
        current = _content_value(current_row)
        # Consent filtering can remove a system/media record between two
        # retained messages.  Those records must remain a hard boundary; only
        # source records that were adjacent in the original export may form a
        # split credential.
        previous_index = _source_record_index(previous_row)
        current_index = _source_record_index(current_row)
        if previous_index is not None and current_index is not None:
            if current_index != previous_index + 1:
                continue
        if _split_secret_value(previous, current) is not None:
            matches += 1
    return matches


def _source_record_index(message: Any) -> int | None:
    if not isinstance(message, Mapping):
        return None
    value = message.get("source_record_index")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def redact_adjacent_messages(messages: Iterable[Any]) -> list[Any]:
    """Redact compact values that follow an adjacent credential label.

    The returned rows preserve their structure and source metadata.  This pass
    runs after consent routing, so a filtered source record remains a boundary
    through the source index check above and cannot create a false adjacency.
    """

    rows = list(messages)
    for position, (previous_row, current_row) in enumerate(zip(rows, rows[1:]), start=1):
        previous = _content_value(previous_row)
        current = _content_value(current_row)
        previous_index = _source_record_index(previous_row)
        current_index = _source_record_index(current_row)
        if previous_index is not None and current_index is not None and current_index != previous_index + 1:
            continue
        candidate = _split_secret_value(previous, current)
        if candidate is None:
            continue
        if isinstance(current_row, Mapping):
            updated = dict(current_row)
            for key in ("content", "text_redacted", "text"):
                if key in updated:
                    original_value = str(updated.get(key) or "")
                    stripped_value = original_value.strip()
                    if stripped_value == candidate:
                        updated[key] = "<SECRET>"
                    else:
                        start = original_value.find(candidate)
                        updated[key] = (
                            original_value[:start]
                            + "<SECRET>"
                            + original_value[start + len(candidate):]
                            if start >= 0
                            else "<SECRET>"
                        )
                    break
            else:
                updated["content"] = "<SECRET>"
            if "redaction_categories" in updated:
                categories = set(updated.get("redaction_categories") or [])
                categories.add("credential")
                updated["redaction_categories"] = sorted(categories)
            rows[position] = updated
        else:
            rows[position] = "<SECRET>"
    return rows


def count_adjacent_secret_redactions(messages: Iterable[Any]) -> int:
    """Count safe ``<SECRET>`` substitutions added by the sequence pass.

    Only aggregate evidence is returned. Existing placeholders are excluded,
    and no matched credential value is retained or exposed.
    """

    before = list(messages)
    after = redact_adjacent_messages(before)
    return sum(
        _content_value(updated).count("<SECRET>")
        - _content_value(original).count("<SECRET>")
        for original, updated in zip(before, after)
    )


def scan_adjacent_messages(messages: Iterable[Any]) -> dict[str, int]:
    """Scan a sequence of structured turns, including split credentials."""

    values = list(messages)
    aggregate: dict[str, int] = {}
    for message in values:
        row = scan_atomic_text(_content_value(message))
        for key, count in row.items():
            if key != "hard_leak_count":
                aggregate[key] = aggregate.get(key, 0) + count
    aggregate["cross_message_secret_matches"] = _adjacent_secret_matches(values)
    aggregate["hard_leak_count"] = sum(aggregate.values())
    return aggregate


def _scan_atomic_messages(messages: Iterable[Any]) -> dict[str, int]:
    """Scan content fields independently, without inferring adjacency."""

    aggregate: dict[str, int] = {}
    for message in messages:
        row = scan_atomic_text(_content_value(message))
        for key, count in row.items():
            if key != "hard_leak_count":
                aggregate[key] = aggregate.get(key, 0) + count
    aggregate["cross_message_secret_matches"] = 0
    aggregate["hard_leak_count"] = sum(aggregate.values())
    return aggregate


def _merge_scan_reports(*reports: Mapping[str, Any]) -> dict[str, int]:
    """Take the maximum count per category across equivalent content views."""

    keys = {
        key
        for report in reports
        for key, value in report.items()
        if key != "hard_leak_count"
        and not isinstance(value, bool)
        and isinstance(value, int)
    }
    merged = {
        key: max(int(report.get(key, 0)) for report in reports)
        for key in keys
    }
    merged["hard_leak_count"] = sum(merged.values())
    return merged


def scan_session(session: Mapping[str, Any]) -> dict[str, Any]:
    """Scan both public turns and their source-boundary representation."""

    turns = session.get("turns") if isinstance(session, Mapping) else None
    public_report = scan_adjacent_messages(turns or [])
    boundary_values: list[Any] = []
    has_boundary_view = False
    for turn in turns or []:
        if isinstance(turn, Mapping) and isinstance(turn.get("message_contents"), list):
            has_boundary_view = True
            indices = turn.get("source_record_indices") or []
            for position, value in enumerate(turn["message_contents"]):
                item = {"content": value}
                if position < len(indices):
                    item["source_record_index"] = indices[position]
                boundary_values.append(item)
        else:
            boundary_values.append(turn)
    result = public_report
    trusted_boundary = (
        isinstance(session, Mapping)
        and session.get("session_rule_version") == "wechat-session-v2"
        and has_boundary_view
        and all(
            isinstance(turn, Mapping)
            and isinstance(turn.get("message_contents"), list)
            and isinstance(turn.get("source_record_indices"), list)
            and len(turn["message_contents"]) == len(turn["source_record_indices"])
            and len(turn["message_contents"]) == len(turn.get("message_ids") or [])
            for turn in turns or []
        )
    )
    if trusted_boundary:
        result = _merge_scan_reports(
            _scan_atomic_messages(turns or []),
            scan_adjacent_messages(boundary_values),
        )
    if isinstance(session, Mapping) and session.get("session_id"):
        result["object_id"] = str(session["session_id"])
    return result


def scan_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Scan public candidate messages and any boundary-preserving view."""

    messages = candidate.get("messages") if isinstance(candidate, Mapping) else None
    public_messages = (
        messages
        if messages is not None
        else ([candidate] if isinstance(candidate, Mapping) else [])
    )
    public_report = scan_adjacent_messages(public_messages)
    boundary_messages = (
        candidate.get("privacy_scan_messages")
        if isinstance(candidate, Mapping)
        else None
    )
    result = public_report
    trusted_boundary = (
        isinstance(candidate, Mapping)
        and candidate.get("privacy_boundary_version") == "wechat-privacy-boundary-v1"
        and isinstance(boundary_messages, list)
        and bool(boundary_messages)
        and all(
            isinstance(message, Mapping)
            and isinstance(message.get("content"), str)
            and isinstance(message.get("source_record_index"), int)
            for message in boundary_messages
        )
    )
    if trusted_boundary:
        result = _merge_scan_reports(
            _scan_atomic_messages(public_messages),
            scan_adjacent_messages(boundary_messages),
        )
    if isinstance(candidate, Mapping) and candidate.get("sample_id"):
        result["object_id"] = str(candidate["sample_id"])
    return result


def scan_redacted_text(text: Any) -> dict[str, int]:
    """Backward-compatible alias for :func:`scan_atomic_text`."""

    return scan_atomic_text(text)
