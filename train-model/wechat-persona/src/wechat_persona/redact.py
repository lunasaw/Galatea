"""Deterministic privacy redaction and a second-pass hard-leak scanner."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


REDACTION_VERSION = "wechat-redaction-v1"
PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])")
ID_RE = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
BANK_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
SECRET_RE = re.compile(r"(?is)(?:密码|口令|验证码|校验码|token|access[_ -]?key|secret|api[_ -]?key|private[_ -]?key)\s*[:：=]?\s*[A-Za-z0-9_+/=.-]{4,}")
COORD_RE = re.compile(r"(?<![\d.])[-+]?\d{1,3}\.\d{4,}\s*[,， ]\s*[-+]?\d{1,3}\.\d{4,}(?![\d.])")
URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
WXID_RE = re.compile(r"\bwxid_[A-Za-z0-9_-]+\b")
ADDRESS_RE = re.compile(r"(?i)(?:地址|住址|定位|位置)\s*[:：]?\s*[^\n，。；;]{2,40}")
MENTION_RE = re.compile(r"@[\w\u4e00-\u9fff·-]{1,30}")


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


def scan_redacted_text(text: Any) -> dict[str, int]:
    value = str(text or "")
    counts = {
        "raw_phone_matches": len(PHONE_RE.findall(value)),
        "raw_email_matches": len(EMAIL_RE.findall(value)),
        "raw_secret_matches": len(SECRET_RE.findall(value)),
        "raw_wxid_matches": len(WXID_RE.findall(value)),
        "raw_idcard_matches": len(ID_RE.findall(value)),
        "raw_coordinate_matches": len(COORD_RE.findall(value)),
    }
    counts["hard_leak_count"] = sum(counts.values())
    return counts
