"""Deterministic, privacy-preserving curation for the reviewed SFT snapshot."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import unicodedata
from typing import Any, Iterable, Iterator

from ._common import canonical_json, digest, file_digest
from .datasets import DATASET_SCHEMA_VERSION, _publish_directory_noreplace
from .redact import scan_candidate
from .review import _content_hash


class CurationError(ValueError):
    pass


SELECTION_VERSION = "girlfriend-assistant-usefulness-v1"
CURATION_SCHEMA_VERSION = "wechat-persona-curated-draft-v1"
SPLITS = ("train", "validation")
HOLDOUT_SPLIT = "test"
CONTROLLED_ROOT = Path("/srv/galatea-private/wechat-persona")


@dataclass(frozen=True)
class CurationPolicy:
    min_assistant_meaningful_chars: int = 3
    min_usefulness_score: int = 5
    multi_turn_context_min_messages: int = 6
    repetitive_template_min_train_frequency: int = 6
    repetitive_template_max_meaningful_chars: int = 32
    repetitive_template_train_cap: int = 3
    repetitive_template_validation_cap: int = 1
    train_user_length_quantile: float = 0.99
    train_assistant_length_quantile: float = 0.99


@dataclass(frozen=True)
class FrozenThresholds:
    user_long_chars: int
    assistant_long_chars: int
    repetitive_template_min_train_frequency: int


@dataclass(frozen=True)
class Observation:
    sample_id: str
    sample_sha256: str
    session_sha256: str
    group_sha256: str | None
    split: str
    source_line_number: int
    user_chars: int
    assistant_chars: int
    assistant_meaningful_chars: int
    context_message_count: int
    assistant_template_sha256: str
    assistant_template_meaningful_chars: int
    categories: tuple[str, ...]
    hard_flags: tuple[str, ...]
    risk_flags: tuple[str, ...]
    work_signal_count: int
    fact_signal_count: int


@dataclass(frozen=True)
class Decision:
    sample_id: str
    sample_sha256: str
    session_sha256: str
    group_sha256: str | None
    split: str
    selected: bool
    usefulness_score: int
    positive_categories: tuple[str, ...]
    reason_codes: tuple[str, ...]
    risk_flags: tuple[str, ...]
    user_chars: int
    assistant_chars: int
    assistant_meaningful_chars: int
    context_message_count: int
    assistant_template_sha256: str


_SPEAKER_RE = re.compile(r"(?mi)^(self|target)\s*[:\uff1a]")
_PLACEHOLDER_RE = re.compile(
    r"(?:<\s*(?:image|video|audio|file|emoji|sticker|media)[^>]*>|"
    r"\[\s*(?:\u56fe\u7247|\u89c6\u9891|\u8bed\u97f3|\u6587\u4ef6|\u8868\u60c5|\u52a8\u753b\u8868\u60c5|"
    r"image|video|audio|file|emoji|sticker|media)\s*\])",
    re.IGNORECASE,
)
_REDACTION_RE = re.compile(r"<(?:SECRET|PII_[A-Z_]+|REDACTED|PHONE|EMAIL|ID)>")
_ALNUM_CJK_RE = re.compile(r"[A-Za-z0-9\u3400-\u9fff]")
_NUMBER_FACT_RE = re.compile(
    r"(?:\d{1,4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?|\d+(?:\.\d+)?(?:\u5e74|\u6708|\u65e5|"
    r"\u70b9|\u5206|\u5143|\u5757|\u516c\u91cc|kg|GB|MB))",
    re.IGNORECASE,
)

_CATEGORY_PATTERNS = {
    "emotional_attunement": re.compile(
        r"\u8f9b\u82e6|\u59d4\u5c48|\u5fc3\u75bc|\u96be\u8fc7|\u4e0d\u5f00\u5fc3|\u522b\u96be\u53d7|\u7406\u89e3\u4f60|\u61c2\u4f60|\u6ca1\u5173\u7cfb"
    ),
    "caring_followup": re.compile(
        r"\u8fd8\u597d\u5417|\u600e\u4e48\u4e86|\u8981\u4e0d\u8981|\u6709\u6ca1\u6709|\u5403\u996d|\u4f11\u606f|\u65e9\u70b9\u7761|"
        r"\u7761\u4e86\u5417|\u8eab\u4f53|\u8212\u670d\u5417|\u5230\u5bb6\u4e86\u5417|\u5fd9\u5b8c\u4e86\u5417"
    ),
    "affectionate": re.compile(
        r"\u5b9d\u8d1d|\u5b9d\u5b9d|\u4eb2\u7231\u7684|\u8001\u516c|\u8001\u5a46|\u60f3\u4f60|\u7231\u4f60|\u62b1\u62b1|\u4eb2\u4eb2|\u966a\u4f60"
    ),
    "life_advice": re.compile(
        r"\u5efa\u8bae|\u53ef\u4ee5\u5148|\u4e0d\u5982|\u8981\u4e0d|\u8bd5\u8bd5|\u8bb0\u5f97|\u522b\u5fd8|\u5148.{0,8}\u518d|\u6162\u6162\u6765"
    ),
    "joint_decision": re.compile(
        r"\u6211\u4eec\u4e00\u8d77|\u4e00\u8d77\u53bb|\u4e00\u8d77\u770b|\u4e00\u8d77\u60f3|\u5546\u91cf|\u4f60\u51b3\u5b9a|\u542c\u4f60\u7684|\u90fd\u53ef\u4ee5|\u8981\u4e0d\u7136"
    ),
    "playful": re.compile(
        r"\u54c8\u54c8|\u563f\u563f|\u563b\u563b|\u7b11\u6b7b|\u9017\u4f60|\u7b28\u86cb|\u8c03\u76ae|\u54fc\u54fc"
    ),
    "comfort_support": re.compile(
        r"\u522b\u6015|\u653e\u5fc3|\u6ca1\u4e8b\u7684|\u6211\u5728|\u966a\u7740\u4f60|\u652f\u6301\u4f60|\u6162\u6162\u6765|\u4f1a\u597d\u7684|\u52a0\u6cb9|\u62b1\u62b1"
    ),
    "warm_tone": re.compile(r"[\u5440\u5566\u5462\u54e6\u561b\u54c7\u8bf6\uff5e~]|\u597d\u54d2|\u55ef\u55ef|\u4e56|\u665a\u5b89|\u65e9\u5b89"),
}
_WORK_RE = re.compile(
    r"\u5de5\u4f5c|\u9879\u76ee|\u4f1a\u8bae|\u5ba2\u6237|\u540c\u4e8b|\u9886\u5bfc|\u9700\u6c42|\u4ee3\u7801|\u6587\u6863|\u65b9\u6848|"
    r"\u8fdb\u5ea6|\u4e1a\u52a1|\u516c\u53f8|\u52a0\u73ed|\u6c47\u62a5|\u4efb\u52a1"
)
_THIRD_PARTY_RE = re.compile(
    r"\u516b\u5366|\u542c\u8bf4|\u5979\u8bf4|\u4ed6\u8bf4|\u524d\u4efb|\u5c0f\u4e09|\u51fa\u8f68|\u8c01\u548c\u8c01|\u540c\u4e8b\u8bf4|\u670b\u53cb\u8bf4"
)
_PHYSICAL_IDENTITY_RE = re.compile(
    r"(?:\u6211|\u54b1\u4eec)(?:\u5728|\u8981|\u53bb|\u6765|\u521a|\u5df2\u7ecf|\u6b63\u5728|\u9a6c\u4e0a|\u51c6\u5907|\u4e00\u4f1a\u513f).{0,8}"
    r"(?:\u5230\u5bb6|\u4e0b\u73ed|\u4e0a\u73ed|\u5f00\u8f66|\u5750\u8f66|\u51fa\u95e8|\u56de\u5bb6|\u6d17\u6fa1|"
    r"\u7761\u89c9|\u5403\u996d|\u505a\u996d|\u4e70\u83dc|\u53d6\u5feb\u9012)"
    r"|\u6211(?:\u5230|\u5728).{0,10}(?:\u5bb6|\u516c\u53f8|\u5355\u4f4d|\u529e\u516c\u5ba4|\u8def\u4e0a|\u8f66\u4e0a)"
    r"|\u6211.{0,6}(?:\u7ed9\u4f60\u6253\u7535\u8bdd|\u53bb\u627e\u4f60|\u6765\u627e\u4f60|\u7ed9\u4f60\u9001|\u7ed9\u4f60\u5bc4|\u7ed9\u4f60\u5e26)"
)
_FALSE_IDENTITY_RE = re.compile(
    r"\u6211\u4e0d\u662f.{0,4}(?:AI|\u673a\u5668\u4eba)|\u6211\u662f\u4f60(?:\u73b0\u5b9e|\u771f\u6b63|\u771f\u5b9e).{0,6}"
    r"(?:\u5973\u670b\u53cb|\u8001\u5a46)|\u6211\u6709\u771f\u5b9e(?:\u8eab\u4f53|\u751f\u6d3b)",
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(
    r"\u4f60(?:\u53ea|\u53ea\u80fd).{0,6}(?:\u5c5e\u4e8e|\u542c).{0,4}\u6211|\u4e0d\u8bb8.{0,10}(?:\u522b\u4eba|\u5f02\u6027|\u670b\u53cb)|"
    r"\u79bb\u4e0d\u5f00\u6211|\u6ca1\u6709\u6211.{0,6}(?:\u4e0d\u884c|\u6d3b\u4e0d\u4e86)|\u5fc5\u987b\u542c\u6211\u7684|\u4f60\u662f\u6211\u7684"
)
_ABUSE_RE = re.compile(
    r"\u4f60\u600e\u4e48\u4e0d\u53bb\u6b7b|\u6eda\u8fdc\u70b9|\u5e9f\u7269|\u4eba\u6e23|\u795e\u7ecf\u75c5|\u50bb\u903c|\u8111\u6b8b"
)

_CATEGORY_WEIGHTS = {
    "engaged_question": 2,
    "emotional_attunement": 2,
    "caring_followup": 2,
    "affectionate": 2,
    "life_advice": 2,
    "joint_decision": 2,
    "playful": 2,
    "comfort_support": 2,
    "warm_tone": 1,
}
_SUBSTANTIVE_STYLE_CATEGORIES = frozenset(
    name for name in _CATEGORY_WEIGHTS if name not in {"engaged_question", "warm_tone"}
)
_PRIMARY_REASON_PRIORITY = (
    "invalid_direct_context",
    "missing_user_context",
    "context_length_outlier",
    "target_length_outlier",
    "redaction_placeholder_target",
    "placeholder_only_target",
    "empty_or_emoji_only_target",
    "assistant_reply_too_short",
    "unsafe_or_controlling_tone",
    "false_human_identity_claim",
    "real_world_identity_claim",
    "structured_memory_candidate_not_style_sft",
    "repetitive_template_cap",
    "long_work_log",
    "third_party_gossip",
    "insufficient_usefulness_score",
    "no_target_style_signal",
)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CurationError(f"{label} must be readable UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise CurationError(f"{label} must contain a JSON object")
    return value


def _ensure_regular_file(path: Path, label: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise CurationError(f"{label} must be a regular non-symlink file")


def _ensure_below(path: Path, root: Path, label: str) -> None:
    resolved = path.resolve()
    controlled = root.resolve()
    if resolved == controlled or controlled not in resolved.parents:
        raise CurationError(f"{label} must be below controlled root {controlled}")


def _quantile(values: list[int], quantile: float) -> int:
    if not values:
        raise CurationError("cannot derive threshold from an empty train split")
    ordered = sorted(values)
    position = min(len(ordered) - 1, math.ceil((len(ordered) - 1) * quantile))
    return int(ordered[position])


def _length_summary(values: Iterable[int]) -> dict[str, Any]:
    rows = list(values)
    if not rows:
        return {"count": 0, "min": None, "p50": None, "p90": None, "p99": None, "max": None}
    return {
        "count": len(rows),
        "min": min(rows),
        "p50": _quantile(rows, 0.50),
        "p90": _quantile(rows, 0.90),
        "p99": _quantile(rows, 0.99),
        "max": max(rows),
    }


def _text_by_role(row: dict[str, Any], role: str) -> str:
    return "\n".join(
        str(message.get("content") or "")
        for message in row.get("messages") or []
        if isinstance(message, dict) and message.get("role") == role
    )


def _meaningful(text: str) -> str:
    return "".join(_ALNUM_CJK_RE.findall(unicodedata.normalize("NFKC", text)))


def _template_digest(text: str) -> tuple[str, int]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = re.sub(r"\d+", "#", normalized)
    normalized = "".join(_ALNUM_CJK_RE.findall(normalized) + (["#"] if "#" in normalized else []))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest(), len(normalized)


def _last_speaker(text: str) -> str | None:
    matches = [match.group(1).lower() for match in _SPEAKER_RE.finditer(text)]
    return matches[-1] if matches else None


def _categories(assistant: str) -> tuple[str, ...]:
    result = {
        name for name, pattern in _CATEGORY_PATTERNS.items() if pattern.search(assistant)
    }
    if "?" in assistant or "\uff1f" in assistant:
        result.add("engaged_question")
    return tuple(sorted(result))


def _observe_row(
    row: dict[str, Any], raw_line: bytes, split: str, line_number: int
) -> Observation:
    sample_id = str(row.get("sample_id") or "")
    session_id = str(row.get("session_id") or "")
    if not sample_id or not session_id:
        raise CurationError(f"{split} row {line_number} is missing sample/session identity")
    metadata = row.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("split") != split:
        raise CurationError(f"{split} row {line_number} has a split mismatch")
    status = metadata.get("review_status")
    if status not in {"keep", "redact_keep"}:
        raise CurationError(f"{split} row {line_number} is not human-approved")
    if not metadata.get("reviewer_id") or not metadata.get("reviewed_at"):
        raise CurationError(f"{split} row {line_number} lacks human-review evidence")
    if metadata.get("assistant_only_loss") is not True:
        raise CurationError(f"{split} row {line_number} lacks assistant-only-loss binding")
    messages = row.get("messages")
    if (
        not isinstance(messages, list)
        or tuple(message.get("role") for message in messages if isinstance(message, dict))
        != ("system", "user", "assistant")
        or len(messages) != 3
    ):
        raise CurationError(f"{split} row {line_number} has an invalid message contract")
    current_hash = _content_hash(row)
    expected_hash = (
        metadata.get("redacted_content_sha256")
        if status == "redact_keep"
        else metadata.get("content_sha256")
    )
    if expected_hash != current_hash:
        raise CurationError(f"{split} row {line_number} content hash mismatch")
    if scan_candidate(row).get("hard_leak_count") != 0:
        raise CurationError(f"{split} row {line_number} failed the privacy rescan")

    user = _text_by_role(row, "user")
    assistant = _text_by_role(row, "assistant")
    meaningful = _meaningful(_PLACEHOLDER_RE.sub("", assistant))
    template_sha256, template_length = _template_digest(assistant)
    categories = _categories(assistant)
    hard_flags: set[str] = set()
    risk_flags: set[str] = set()

    if _last_speaker(user) == "target":
        hard_flags.add("invalid_direct_context")
    if not _meaningful(user):
        hard_flags.add("missing_user_context")
    if _REDACTION_RE.search(assistant):
        hard_flags.add("redaction_placeholder_target")
    if _PLACEHOLDER_RE.fullmatch(assistant.strip()):
        hard_flags.add("placeholder_only_target")
    if not meaningful:
        hard_flags.add("empty_or_emoji_only_target")
    if _CONTROL_RE.search(assistant) or _ABUSE_RE.search(assistant):
        hard_flags.add("unsafe_or_controlling_tone")
    if _FALSE_IDENTITY_RE.search(assistant):
        hard_flags.add("false_human_identity_claim")
    if _PHYSICAL_IDENTITY_RE.search(assistant):
        hard_flags.add("real_world_identity_claim")

    combined = f"{user}\n{assistant}"
    work_signal_count = len(_WORK_RE.findall(combined))
    fact_signal_count = len(_NUMBER_FACT_RE.findall(combined))
    if _THIRD_PARTY_RE.search(combined):
        risk_flags.add("third_party_gossip")
    if _PLACEHOLDER_RE.search(assistant):
        risk_flags.add("contains_media_placeholder")

    return Observation(
        sample_id=sample_id,
        sample_sha256=hashlib.sha256(raw_line).hexdigest(),
        session_sha256=hashlib.sha256(session_id.encode("utf-8")).hexdigest(),
        group_sha256=(
            hashlib.sha256(str(metadata["near_duplicate_group"]).encode("utf-8")).hexdigest()
            if metadata.get("near_duplicate_group")
            else None
        ),
        split=split,
        source_line_number=line_number,
        user_chars=len(user),
        assistant_chars=len(assistant),
        assistant_meaningful_chars=len(meaningful),
        context_message_count=len(metadata.get("context_message_ids") or []),
        assistant_template_sha256=template_sha256,
        assistant_template_meaningful_chars=template_length,
        categories=categories,
        hard_flags=tuple(sorted(hard_flags)),
        risk_flags=tuple(sorted(risk_flags)),
        work_signal_count=work_signal_count,
        fact_signal_count=fact_signal_count,
    )


def _iter_jsonl(path: Path, split: str) -> Iterator[tuple[dict[str, Any], bytes, int]]:
    with path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CurationError(f"invalid JSON in {split} row {line_number}") from exc
            if not isinstance(value, dict):
                raise CurationError(f"{split} row {line_number} must be an object")
            yield value, raw_line, line_number


def _load_observations(path: Path, split: str) -> list[Observation]:
    return [
        _observe_row(row, raw_line, split, line_number)
        for row, raw_line, line_number in _iter_jsonl(path, split)
    ]


def _derive_thresholds(train: list[Observation], policy: CurationPolicy) -> FrozenThresholds:
    return FrozenThresholds(
        user_long_chars=_quantile(
            [row.user_chars for row in train], policy.train_user_length_quantile
        ),
        assistant_long_chars=_quantile(
            [row.assistant_chars for row in train],
            policy.train_assistant_length_quantile,
        ),
        repetitive_template_min_train_frequency=policy.repetitive_template_min_train_frequency,
    )


def _preliminary_decision(
    row: Observation,
    *,
    policy: CurationPolicy,
    thresholds: FrozenThresholds,
) -> Decision:
    reasons = set(row.hard_flags)
    risks = set(row.risk_flags)
    categories = set(row.categories)
    score = 2

    if row.assistant_meaningful_chars < policy.min_assistant_meaningful_chars:
        reasons.add("assistant_reply_too_short")
    if row.user_chars > thresholds.user_long_chars * 2:
        reasons.add("context_length_outlier")
    if row.assistant_chars > thresholds.assistant_long_chars * 4:
        reasons.add("target_length_outlier")

    if row.assistant_chars <= thresholds.assistant_long_chars:
        score += 1
    else:
        score -= 1
        risks.add("long_assistant_response")
    if row.context_message_count >= policy.multi_turn_context_min_messages:
        score += 1
    score += sum(_CATEGORY_WEIGHTS[name] for name in categories)

    if row.user_chars > thresholds.user_long_chars and row.work_signal_count >= 3:
        score -= 3
        risks.add("long_work_log")
    if "third_party_gossip" in risks:
        score -= 2
    if row.fact_signal_count >= 3 and not (categories & _SUBSTANTIVE_STYLE_CATEGORIES):
        reasons.add("structured_memory_candidate_not_style_sft")

    if not categories:
        reasons.add("no_target_style_signal")
    if score < policy.min_usefulness_score:
        reasons.add("insufficient_usefulness_score")
    if "long_work_log" in risks and score < policy.min_usefulness_score:
        reasons.add("long_work_log")
    if "third_party_gossip" in risks and score < policy.min_usefulness_score:
        reasons.add("third_party_gossip")

    selected = not reasons
    return Decision(
        sample_id=row.sample_id,
        sample_sha256=row.sample_sha256,
        session_sha256=row.session_sha256,
        group_sha256=row.group_sha256,
        split=row.split,
        selected=selected,
        usefulness_score=score,
        positive_categories=tuple(sorted(categories)),
        reason_codes=("selected_useful_style",) if selected else tuple(sorted(reasons)),
        risk_flags=tuple(sorted(risks)),
        user_chars=row.user_chars,
        assistant_chars=row.assistant_chars,
        assistant_meaningful_chars=row.assistant_meaningful_chars,
        context_message_count=row.context_message_count,
        assistant_template_sha256=row.assistant_template_sha256,
    )


def _apply_repetition_caps(
    observations: list[Observation],
    decisions: list[Decision],
    *,
    repetitive_templates: set[str],
    cap: int,
) -> list[Decision]:
    eligible: dict[str, list[Decision]] = defaultdict(list)
    for decision in decisions:
        if decision.selected and decision.assistant_template_sha256 in repetitive_templates:
            eligible[decision.assistant_template_sha256].append(decision)
    rejected_ids: set[str] = set()
    for rows in eligible.values():
        ranked = sorted(
            rows,
            key=lambda item: hashlib.sha256(item.sample_id.encode("utf-8")).hexdigest(),
        )
        rejected_ids.update(row.sample_id for row in ranked[cap:])
    return [
        replace(
            decision,
            selected=False,
            reason_codes=("repetitive_template_cap",),
        )
        if decision.sample_id in rejected_ids
        else decision
        for decision in decisions
    ]


def _decision_record(decision: Decision) -> dict[str, Any]:
    value = asdict(decision)
    value["positive_categories"] = list(decision.positive_categories)
    value["reason_codes"] = list(decision.reason_codes)
    value["risk_flags"] = list(decision.risk_flags)
    return value


def _primary_reason(decision: Decision) -> str:
    if decision.selected:
        return "selected_useful_style"
    reasons = set(decision.reason_codes)
    return next((reason for reason in _PRIMARY_REASON_PRIORITY if reason in reasons), "excluded_other")


def _split_report(
    observations: list[Observation], decisions: list[Decision]
) -> dict[str, Any]:
    selected_ids = {row.sample_id for row in decisions if row.selected}
    selected = [row for row in observations if row.sample_id in selected_ids]
    category_counts = Counter(
        category for row in decisions if row.selected for category in row.positive_categories
    )
    reason_counts = Counter(
        reason for row in decisions if not row.selected for reason in row.reason_codes
    )
    primary_counts = Counter(_primary_reason(row) for row in decisions if not row.selected)
    selected_risk_counts = Counter(
        risk for row in decisions if row.selected for risk in row.risk_flags
    )

    def duplicate_summary(rows: list[Observation]) -> dict[str, Any]:
        counts = Counter(row.assistant_template_sha256 for row in rows)
        repeated = {key: count for key, count in counts.items() if count > 1}
        repeated_rows = sum(repeated.values())
        return {
            "normalized_response_unique_count": len(counts),
            "repeated_group_count": len(repeated),
            "rows_in_repeated_groups": repeated_rows,
            "rows_in_repeated_groups_rate": round(repeated_rows / len(rows), 6) if rows else 0.0,
        }

    return {
        "parent_count": len(observations),
        "selected_count": len(selected),
        "excluded_count": len(observations) - len(selected),
        "selection_rate": round(len(selected) / len(observations), 6) if observations else 0.0,
        "selected_category_counts": dict(sorted(category_counts.items())),
        "exclusion_reason_counts": dict(sorted(reason_counts.items())),
        "primary_exclusion_reason_counts": dict(sorted(primary_counts.items())),
        "selected_risk_flag_counts": dict(sorted(selected_risk_counts.items())),
        "parent_lengths": {
            "user_chars": _length_summary(row.user_chars for row in observations),
            "assistant_chars": _length_summary(row.assistant_chars for row in observations),
        },
        "selected_lengths": {
            "user_chars": _length_summary(row.user_chars for row in selected),
            "assistant_chars": _length_summary(row.assistant_chars for row in selected),
        },
        "parent_duplicate_aggregate": duplicate_summary(observations),
        "selected_duplicate_aggregate": duplicate_summary(selected),
    }


def _validate_parent(
    parent_snapshot: Path, review_summary_path: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    manifest_path = parent_snapshot / "manifest.json"
    split_paths = {split: parent_snapshot / f"{split}.jsonl" for split in (*SPLITS, HOLDOUT_SPLIT)}
    for label, path in {"parent manifest": manifest_path, "review summary": review_summary_path, **{f"parent {split}": path for split, path in split_paths.items()}}.items():
        _ensure_regular_file(path, label)
    manifest = _read_json(manifest_path, "parent manifest")
    review = _read_json(review_summary_path, "review summary")
    if manifest.get("schema_version") != DATASET_SCHEMA_VERSION:
        raise CurationError("parent snapshot schema is not wechat-sft-v2")
    if manifest.get("assistant_only_loss") is not True:
        raise CurationError("parent snapshot is not assistant-only-loss SFT")
    for layer in ("reviewed", "formal_snapshot"):
        hard_leaks = ((manifest.get("privacy_counts") or {}).get(layer) or {}).get("hard_leak_count")
        if isinstance(hard_leaks, bool) or hard_leaks != 0:
            raise CurationError(f"parent privacy gate is not zero: {layer}")
    if (
        review.get("status") != "completed"
        or review.get("human_review_completed") is not True
        or review.get("uncertain_count") != 0
        or review.get("reviewed_hard_leak_count") != 0
    ):
        raise CurationError("review summary does not prove completed zero-leak human review")
    if manifest.get("review_evidence_digest") != review.get("review_evidence_digest"):
        raise CurationError("review evidence digest mismatch")
    declared_hashes = manifest.get("split_file_sha256") or {}
    observed_hashes = {split: file_digest(path) for split, path in split_paths.items()}
    if observed_hashes != declared_hashes:
        raise CurationError("parent split file digest mismatch")
    return manifest, review, observed_hashes


def _validate_identity_boundaries(
    train: list[Observation], validation: list[Observation]
) -> None:
    seen_ids: set[str] = set()
    sessions: dict[str, str] = {}
    groups: dict[str, str] = {}
    for row in [*train, *validation]:
        if row.sample_id in seen_ids:
            raise CurationError("duplicate sample ID across observed splits")
        seen_ids.add(row.sample_id)
        prior_split = sessions.get(row.session_sha256)
        if prior_split and prior_split != row.split:
            raise CurationError("session crosses train and validation")
        sessions[row.session_sha256] = row.split
        if row.group_sha256:
            prior_group_split = groups.get(row.group_sha256)
            if prior_group_split and prior_group_split != row.split:
                raise CurationError("near-duplicate group crosses train and validation")
            groups[row.group_sha256] = row.split


def _selection_digest(decisions: dict[str, list[Decision]]) -> str:
    return digest(
        {
            split: [
                {
                    "sample_id": row.sample_id,
                    "sample_sha256": row.sample_sha256,
                    "selected": row.selected,
                    "score": row.usefulness_score,
                    "reason_codes": row.reason_codes,
                }
                for row in decisions[split]
            ]
            for split in SPLITS
        }
    )


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _write_selected_split(
    source: Path,
    destination: Path,
    split: str,
    selected_ids: set[str],
) -> int:
    count = 0
    found: set[str] = set()
    with destination.open("xb") as output:
        for row, raw_line, _line_number in _iter_jsonl(source, split):
            sample_id = str(row.get("sample_id") or "")
            if sample_id in selected_ids:
                output.write(raw_line if raw_line.endswith(b"\n") else raw_line + b"\n")
                found.add(sample_id)
                count += 1
    destination.chmod(stat.S_IRUSR | stat.S_IWUSR)
    if found != selected_ids:
        raise CurationError(f"selected {split} IDs changed between planning and write")
    return count


def curate_snapshot(
    *,
    parent_snapshot: Path,
    review_summary_path: Path,
    output_root: Path,
    execute: bool = False,
    controlled_root: Path = CONTROLLED_ROOT,
    policy: CurationPolicy | None = None,
) -> dict[str, Any]:
    """Plan or atomically publish a private curated train/validation draft."""

    policy = policy or CurationPolicy()
    parent_snapshot = parent_snapshot.resolve()
    review_summary_path = review_summary_path.resolve()
    output_root = output_root.resolve()
    controlled_root = controlled_root.resolve()
    _ensure_below(parent_snapshot, controlled_root, "parent snapshot")
    _ensure_below(review_summary_path, controlled_root, "review summary")
    _ensure_below(output_root, controlled_root, "output root")

    parent_manifest, review_summary, parent_hashes = _validate_parent(
        parent_snapshot, review_summary_path
    )
    train = _load_observations(parent_snapshot / "train.jsonl", "train")
    parent_counts = parent_manifest.get("sample_counts") or {}
    if len(train) != parent_counts.get("train"):
        raise CurationError("parent train count mismatch")

    thresholds = _derive_thresholds(train, policy)
    train_template_counts = Counter(row.assistant_template_sha256 for row in train)
    repetitive_templates = {
        row.assistant_template_sha256
        for row in train
        if row.assistant_template_meaningful_chars
        <= policy.repetitive_template_max_meaningful_chars
        and train_template_counts[row.assistant_template_sha256]
        >= thresholds.repetitive_template_min_train_frequency
    }
    train_decisions = [
        _preliminary_decision(row, policy=policy, thresholds=thresholds) for row in train
    ]
    train_decisions = _apply_repetition_caps(
        train,
        train_decisions,
        repetitive_templates=repetitive_templates,
        cap=policy.repetitive_template_train_cap,
    )

    # Validation is loaded only after all data-dependent thresholds and the
    # repeated-template set have been frozen from train.
    validation = _load_observations(
        parent_snapshot / "validation.jsonl", "validation"
    )
    if len(validation) != parent_counts.get("validation"):
        raise CurationError("parent validation count mismatch")
    _validate_identity_boundaries(train, validation)
    validation_decisions = [
        _preliminary_decision(row, policy=policy, thresholds=thresholds)
        for row in validation
    ]
    validation_decisions = _apply_repetition_caps(
        validation,
        validation_decisions,
        repetitive_templates=repetitive_templates,
        cap=policy.repetitive_template_validation_cap,
    )
    decisions = {"train": train_decisions, "validation": validation_decisions}
    observations = {"train": train, "validation": validation}
    selection_sha256 = _selection_digest(decisions)
    policy_sha256 = digest({"selection_version": SELECTION_VERSION, "policy": asdict(policy)})
    curation_digest = digest(
        {
            "parent_manifest_file_sha256": file_digest(parent_snapshot / "manifest.json"),
            "selection_policy_sha256": policy_sha256,
            "selection_sha256": selection_sha256,
        }
    )
    curation_id = (
        f"{parent_manifest.get('dataset_id', 'wechat')}-curated-draft-v1-"
        f"{curation_digest[:16]}"
    )
    output = output_root / curation_id
    quality_report = {
        "schema_version": CURATION_SCHEMA_VERSION,
        "selection_version": SELECTION_VERSION,
        "selection_policy_sha256": policy_sha256,
        "selection_sha256": selection_sha256,
        "curation_digest": curation_digest,
        "threshold_basis": "train-only; frozen before validation",
        "thresholds": asdict(thresholds),
        "policy": asdict(policy),
        "splits": {
            split: _split_report(observations[split], decisions[split])
            for split in SPLITS
        },
        "repetition_policy": {
            "train_known_repetitive_template_count": len(repetitive_templates),
            "validation_threshold_or_template_learning": False,
        },
        "privacy": {
            "raw_text_in_report": False,
            "private_text_samples_in_report": 0,
            "row_privacy_rescan_hard_leak_count": 0,
        },
        "memory_boundary": {
            "structured_memory_exported": False,
            "fact_dominant_rows_are_not_treated_as_style_value": True,
        },
        "parent_test_untouched": True,
        "training_eligible": False,
        "human_review_required": True,
        "formal_approval_required": True,
    }
    selected_counts = {
        split: sum(row.selected for row in decisions[split]) for split in SPLITS
    }
    plan = {
        "curation_id": curation_id,
        "output_directory": str(output),
        "will_write": execute,
        "selection_version": SELECTION_VERSION,
        "selection_policy_sha256": policy_sha256,
        "selection_sha256": selection_sha256,
        "curation_digest": curation_digest,
        "parent_counts": {
            "train": len(train),
            "validation": len(validation),
            "test": parent_counts.get("test"),
        },
        "selected_counts": selected_counts,
        "parent_test_untouched": True,
        "training_eligible": False,
        "human_review_required": True,
        "formal_approval_required": True,
    }
    if not execute:
        return {**plan, "quality_report": quality_report}
    if output.exists():
        raise FileExistsError(f"curated output already exists: {output}")
    if output_root.exists():
        if not output_root.is_dir() or output_root.is_symlink():
            raise CurationError("output root must be a non-symlink directory")
    else:
        output_root.mkdir(parents=True, mode=stat.S_IRWXU)
        output_root.chmod(stat.S_IRWXU)
    staging = Path(tempfile.mkdtemp(prefix=f".{curation_id}.staging-", dir=output_root))
    staging.chmod(stat.S_IRWXU)
    try:
        for split in SPLITS:
            selected_ids = {row.sample_id for row in decisions[split] if row.selected}
            observed_count = _write_selected_split(
                parent_snapshot / f"{split}.jsonl",
                staging / f"{split}.jsonl",
                split,
                selected_ids,
            )
            if observed_count != selected_counts[split]:
                raise CurationError(f"selected {split} count mismatch during write")

        decisions_path = staging / "selection-decisions.jsonl"
        with decisions_path.open("x", encoding="utf-8") as handle:
            for split in SPLITS:
                for decision in decisions[split]:
                    handle.write(canonical_json(_decision_record(decision)) + "\n")
        decisions_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        quality_path = staging / "quality-report.json"
        _write_json(quality_path, quality_report)

        artifact_hashes = {
            "train.jsonl": file_digest(staging / "train.jsonl"),
            "validation.jsonl": file_digest(staging / "validation.jsonl"),
            "selection-decisions.jsonl": file_digest(decisions_path),
            "quality-report.json": file_digest(quality_path),
        }
        manifest = {
            "schema_version": CURATION_SCHEMA_VERSION,
            "curation_id": curation_id,
            "selection_version": SELECTION_VERSION,
            "selection_policy_sha256": policy_sha256,
            "selection_sha256": selection_sha256,
            "curation_digest": curation_digest,
            "parent": {
                "dataset_id": parent_manifest.get("dataset_id"),
                "snapshot_path": str(parent_snapshot),
                "manifest_path": str(parent_snapshot / "manifest.json"),
                "manifest_file_sha256": file_digest(parent_snapshot / "manifest.json"),
                "review_summary_path": str(review_summary_path),
                "review_summary_file_sha256": file_digest(review_summary_path),
                "review_evidence_digest": review_summary.get("review_evidence_digest"),
                "split_sha256": parent_manifest.get("split_sha256"),
                "splits": {
                    "train": {
                        "path": str(parent_snapshot / "train.jsonl"),
                        "sha256": parent_hashes["train"],
                        "count": len(train),
                        "content_parsed": True,
                    },
                    "validation": {
                        "path": str(parent_snapshot / "validation.jsonl"),
                        "sha256": parent_hashes["validation"],
                        "count": len(validation),
                        "content_parsed": True,
                    },
                    "test": {
                        "path": str(parent_snapshot / "test.jsonl"),
                        "sha256": parent_hashes["test"],
                        "count": parent_counts.get("test"),
                        "content_parsed": False,
                        "bytes_sha256_verified_only": True,
                        "untouched": True,
                    },
                },
            },
            "selected_counts": selected_counts,
            "selected_sample_ids_sha256": {
                split: digest([row.sample_id for row in decisions[split] if row.selected])
                for split in SPLITS
            },
            "selected_session_hashes_sha256": {
                split: digest(
                    sorted({row.session_sha256 for row in decisions[split] if row.selected})
                )
                for split in SPLITS
            },
            "cross_split_session_count": 0,
            "cross_split_near_duplicate_group_count": 0,
            "artifacts_sha256": artifact_hashes,
            "text_bearing_artifacts": ["train.jsonl", "validation.jsonl"],
            "hash_count_only_artifacts": [
                "selection-decisions.jsonl",
                "quality-report.json",
                "selection-manifest.json",
            ],
            "test_artifact_created": False,
            "parent_test_untouched": True,
            "training_eligible": False,
            "formal_training_eligible": False,
            "human_review_required": True,
            "formal_approval_required": True,
            "optimizer_steps": 0,
            "mlflow_run_created": False,
            "ray_job_submitted": False,
        }
        manifest["manifest_content_sha256"] = digest(manifest)
        _write_json(staging / "selection-manifest.json", manifest)
        _publish_directory_noreplace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return {
        **plan,
        "artifact_sha256": {
            **artifact_hashes,
            "selection-manifest.json": file_digest(output / "selection-manifest.json"),
        },
        "quality_report": quality_report,
    }
