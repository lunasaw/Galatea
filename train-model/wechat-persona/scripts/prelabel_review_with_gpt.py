#!/usr/bin/env python3
"""Pre-label the current review draft with an OpenAI-compatible GPT API.

The script deliberately writes through the review server's existing decision API.
It does not create a second annotation file, read ``test.jsonl``, edit candidate
text, or claim that a machine decision is human review.  Every decision is marked
with ``gpt-prelabel-v1`` so a human can filter, inspect, and overwrite it later.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


ALLOWED_LABELS = {
    "direct_response",
    "emotional_attunement",
    "caring_followup",
    "practical_help",
    "playful_affection",
    "relationship_context",
    "repair_boundary",
}
STATUSES = {"keep", "reject", "uncertain"}
MACHINE_REVIEWER_ID = "gpt-prelabel-v1"
DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_OPENAI_BASE_URL = "https://ai.vdian.net/openai"
DEFAULT_REVIEW_URL = "http://127.0.0.1:51644"
DEFAULT_PAGE_SIZE = 100
DEFAULT_BATCH_SIZE = 12
MAX_NOTES_CHARS = 1800
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


SYSTEM_PROMPT = """你是私聊助手 SFT 数据的预审核器。你只提供机器预标注，不能替代人工审核。

判断目标：这条候选是否值得保留为通用、健康、上下文相关的助手回复行为。按以下规则执行：
1. keep：回复有明确意义、回应上下文、可迁移到助手场景，且没有隐私、第三方敏感信息、操纵控制、辱骂、虚假真人身份或线下能力宣称。
2. reject：明显空泛/无关/截断/模板噪声，或命中隐私、第三方八卦、操纵控制、辱骂、虚假真人身份、危险建议等硬风险。
3. uncertain：证据不足、语义依赖隐含线下事实、风险边界不清，或者你对 keep/reject 的把握不足。
4. 不要把“像情侣”当作保留理由；relationship_context 只在人物关系或关系边界本身显著解释该回复、并且对助手行为学习有帮助时使用。
5. 只给出下列用途标签，可多选：direct_response、emotional_attunement、caring_followup、practical_help、playful_affection、relationship_context、repair_boundary。
6. 不做脱敏编辑；不要返回 redact_keep。不要复述原文，不要在 rationale 中引用姓名、联系方式、地址、账号、数字或原句。
7. confidence 是 0 到 1 的小数。rationale 只写不超过 120 个中文字符的理由和风险摘要，不包含候选原文。

输入包含 candidates 数组，每项只有批次内 index 和 messages。必须为每个 index 返回且只返回一个结果，不要 Markdown：
{"results":[{"index":0,"status":"keep|reject|uncertain","labels":["..."],"confidence":0.0,"reason_code":"short_controlled_code","rationale":"brief_non_sensitive_reason","risk_flags":["privacy|third_party|identity|control|abuse|off_context|low_signal|none"]}]}
"""


OUTPUT_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "index",
        "status",
        "labels",
        "confidence",
        "reason_code",
        "rationale",
        "risk_flags",
    ],
    "properties": {
        "index": {"type": "integer", "minimum": 0},
        "status": {"type": "string", "enum": ["keep", "reject", "uncertain"]},
        "labels": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(ALLOWED_LABELS)},
            "maxItems": len(ALLOWED_LABELS),
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason_code": {"type": "string", "minLength": 1, "maxLength": 80},
        "rationale": {"type": "string", "maxLength": 240},
        "risk_flags": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "privacy",
                    "third_party",
                    "identity",
                    "control",
                    "abuse",
                    "off_context",
                    "low_signal",
                    "none",
                ],
            },
            "maxItems": 8,
        },
    },
}


class PrelabelError(RuntimeError):
    """Raised for an invalid API response or a failed review write."""


@dataclass(frozen=True)
class Settings:
    review_url: str
    openai_base_url: str
    model: str
    api_key: str
    page_size: int = DEFAULT_PAGE_SIZE
    batch_size: int = DEFAULT_BATCH_SIZE
    workers: int = 4
    min_confidence: float = 0.78
    request_timeout: float = 90.0
    max_retries: int = 3


def _json_request(
    url: str,
    *,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float,
    max_retries: int,
) -> Any:
    body = None
    request_headers = {"Accept": "application/json", **dict(headers or {})}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        request_headers.setdefault("Content-Type", "application/json")
    retryable = {429, 500, 502, 503, 504}
    for attempt in range(max_retries + 1):
        request = Request(url, data=body, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
            return json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            if exc.code not in retryable or attempt >= max_retries:
                raise PrelabelError(f"HTTP {exc.code} from {url}") from exc
        except (TimeoutError, URLError, OSError, json.JSONDecodeError) as exc:
            if attempt >= max_retries:
                raise PrelabelError(f"request failed for {url}") from exc
        time.sleep(min(2**attempt, 8))
    raise AssertionError("unreachable")


def _response_text(response: Mapping[str, Any]) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    output = response.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, Mapping):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for chunk in content:
                if isinstance(chunk, Mapping) and isinstance(chunk.get("text"), str):
                    parts.append(chunk["text"])
        if parts:
            return "".join(parts).strip()
    raise PrelabelError("OpenAI response did not contain output text")


def _validate_label(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PrelabelError("GPT output must be a JSON object")
    status = value.get("status")
    labels = value.get("labels")
    confidence = value.get("confidence")
    if status not in STATUSES or not isinstance(labels, list):
        raise PrelabelError("GPT output has an invalid status or labels field")
    if any(label not in ALLOWED_LABELS for label in labels) or len(set(labels)) != len(labels):
        raise PrelabelError("GPT output has an invalid usefulness label")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise PrelabelError("GPT output has an invalid confidence")
    confidence = max(0.0, min(1.0, float(confidence)))
    reason_code = str(value.get("reason_code") or "model_review")[:80]
    rationale = re.sub(r"\s+", " ", str(value.get("rationale") or "")).strip()
    rationale = re.sub(r"[\\r\\n]", " ", rationale)[:240]
    risk_flags = value.get("risk_flags")
    if not isinstance(risk_flags, list):
        risk_flags = []
    risk_flags = [str(flag) for flag in risk_flags if str(flag) in {
        "privacy", "third_party", "identity", "control", "abuse", "off_context", "low_signal", "none"
    }]
    return {
        "status": status,
        "labels": sorted(set(labels)),
        "confidence": confidence,
        "reason_code": reason_code,
        "rationale": rationale,
        "risk_flags": sorted(set(risk_flags)),
    }


def _parse_labels(response: Mapping[str, Any], expected_count: int) -> list[dict[str, Any]]:
    text = _response_text(response)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(text)
        if not match:
            raise PrelabelError("GPT output was not JSON")
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise PrelabelError("GPT output contained invalid JSON") from exc
    if not isinstance(value, dict) or not isinstance(value.get("results"), list):
        raise PrelabelError("GPT output must contain a results array")
    by_index: dict[int, dict[str, Any]] = {}
    for item in value["results"]:
        if not isinstance(item, dict):
            raise PrelabelError("GPT result must be a JSON object")
        index = item.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise PrelabelError("GPT result has an invalid index")
        if index in by_index or index < 0 or index >= expected_count:
            raise PrelabelError("GPT result has a duplicate or out-of-range index")
        by_index[index] = _validate_label(item)
    if set(by_index) != set(range(expected_count)):
        raise PrelabelError("GPT result did not cover every candidate")
    return [by_index[index] for index in range(expected_count)]


def _candidate_messages(row: Mapping[str, Any]) -> list[dict[str, str]]:
    messages = row.get("messages")
    if not isinstance(messages, list):
        raise PrelabelError("candidate messages are missing")
    bounded: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "")
        content = message.get("content")
        if role in {"system", "user", "assistant"} and isinstance(content, str):
            bounded.append({"role": role, "content": content})
    if not bounded or not any(item["role"] == "assistant" for item in bounded):
        raise PrelabelError("candidate has no assistant message")
    return bounded


def _batch_schema(batch_size: int, *, resolve_uncertain: bool = False) -> dict[str, Any]:
    item_schema = copy.deepcopy(OUTPUT_ITEM_SCHEMA)
    if resolve_uncertain:
        item_schema["properties"]["status"]["enum"] = ["keep", "reject"]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["results"],
        "properties": {
            "results": {
                "type": "array",
                "items": item_schema,
                "minItems": batch_size,
                "maxItems": batch_size,
            }
        },
    }


def _gpt_labels(
    rows: list[Mapping[str, Any]],
    settings: Settings,
    *,
    resolve_uncertain: bool = False,
) -> list[dict[str, Any]]:
    candidate_input = {
        "candidates": [
            {"index": index, "messages": _candidate_messages(row)}
            for index, row in enumerate(rows)
        ]
    }
    prompt = SYSTEM_PROMPT
    if resolve_uncertain:
        prompt += (
            "\n这是机器待定项的第二次裁决。status 必须是 keep 或 reject；"
            "仍不确定、低信心或有任何硬风险时选择 reject，不得返回 uncertain。"
        )
    payload = {
        "model": settings.model,
        "store": False,
        "max_output_tokens": max(600, len(rows) * 400),
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": prompt}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            candidate_input,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "review_label",
                "strict": True,
                "schema": _batch_schema(
                    len(rows), resolve_uncertain=resolve_uncertain
                ),
            }
        },
    }
    response = _json_request(
        urljoin(settings.openai_base_url.rstrip("/") + "/", "v1/responses"),
        method="POST",
        payload=payload,
        headers={"Authorization": f"Bearer {settings.api_key}"},
        timeout=settings.request_timeout,
        max_retries=settings.max_retries,
    )
    if not isinstance(response, Mapping):
        raise PrelabelError("OpenAI response must be a JSON object")
    return _parse_labels(response, len(rows))


def _effective_decision(
    label: Mapping[str, Any],
    min_confidence: float,
    *,
    resolve_uncertain: bool = False,
) -> dict[str, Any]:
    status = str(label["status"])
    labels = list(label["labels"])
    confidence = float(label["confidence"])
    risks = set(label.get("risk_flags") or [])
    fallback = "reject" if resolve_uncertain else "uncertain"
    if status == "keep" and (confidence < min_confidence or not labels):
        status = fallback
    if status == "keep" and risks.intersection(
        {"privacy", "third_party", "identity", "control", "abuse"}
    ):
        status = fallback
    if status == "reject" and confidence < min_confidence:
        status = fallback
    return {**label, "status": status}


def _notes(label: Mapping[str, Any], model: str) -> str:
    rationale = str(label.get("rationale") or "无附加理由")
    return (
        f"GPT预标注 v1；model={model}；confidence={float(label['confidence']):.2f}；"
        f"reason={str(label.get('reason_code') or 'model_review')[:80]}；{rationale}"
    )[:MAX_NOTES_CHARS]


def _save_decision(row: Mapping[str, Any], label: Mapping[str, Any], settings: Settings) -> None:
    status = str(label["status"])
    reason = None
    if status == "reject":
        reason = f"GPT预标注：{str(label.get('reason_code') or 'model_review')[:80]}"
    payload = {
        "sample_id": row.get("sample_id"),
        "session_id": row.get("session_id") or "",
        "review_status": status,
        "reviewer_id": MACHINE_REVIEWER_ID,
        "review_reason": reason,
        "notes": _notes(label, settings.model),
        "labels": label["labels"],
        "content_sha256": (row.get("metadata") or {}).get("content_sha256"),
        "edited_reply": None,
    }
    result = _json_request(
        urljoin(settings.review_url.rstrip("/") + "/", "api/decision"),
        method="POST",
        payload=payload,
        timeout=settings.request_timeout,
        max_retries=settings.max_retries,
    )
    if not isinstance(result, Mapping) or result.get("status") != "saved":
        raise PrelabelError("review server did not confirm the decision")


def _process_batch(
    rows: list[dict[str, Any]],
    settings: Settings,
    *,
    resolve_uncertain: bool = False,
) -> None:
    labels = _gpt_labels(rows, settings, resolve_uncertain=resolve_uncertain)
    for row, label in zip(rows, labels, strict=True):
        _save_decision(
            row,
            _effective_decision(
                label,
                settings.min_confidence,
                resolve_uncertain=resolve_uncertain,
            ),
            settings,
        )


def _load_candidates(
    settings: Settings, *, max_samples: int = 0
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    bootstrap = _json_request(
        urljoin(settings.review_url.rstrip("/") + "/", "api/bootstrap"),
        timeout=settings.request_timeout,
        max_retries=settings.max_retries,
    )
    existing = {
        str(event.get("sample_id")): dict(event)
        for event in (bootstrap.get("decisions") or [])
        if isinstance(event, Mapping) and event.get("sample_id")
    }
    rows: list[dict[str, Any]] = []
    for split in ("train", "validation"):
        offset = 0
        while True:
            page = _json_request(
                urljoin(settings.review_url.rstrip("/") + "/", "api/dataset")
                + f"?split={split}&offset={offset}&limit={settings.page_size}",
                timeout=settings.request_timeout,
                max_retries=settings.max_retries,
            )
            if not isinstance(page, Mapping) or not isinstance(page.get("rows"), list):
                raise PrelabelError("review server returned an invalid dataset page")
            rows.extend(row for row in page["rows"] if isinstance(row, dict))
            if max_samples > 0 and len(rows) >= max_samples:
                return rows[:max_samples], existing
            offset += len(page["rows"])
            if not page.get("has_more"):
                break
    return rows, existing


def _load_api_key(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("OPENAI_API_KEY")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrelabelError(f"cannot read API key file: {path}") from exc
    if not isinstance(value, str) or not value.strip():
        raise PrelabelError(f"API key file has no OPENAI_API_KEY: {path}")
    return value.strip()


def _arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-url", default=os.getenv("REVIEW_SERVER_URL", DEFAULT_REVIEW_URL))
    parser.add_argument(
        "--openai-base-url",
        default=os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
        help="OpenAI-compatible endpoint root (the script appends /v1/responses)",
    )
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--auth-file", type=Path, default=Path.home() / ".codex" / "auth.json")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--min-confidence", type=float, default=0.78)
    parser.add_argument("--request-timeout", type=float, default=90.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument(
        "--max-samples",
        type=int,
        default=0,
        help="bounded run for a smoke check; 0 means all currently uncertain candidates",
    )
    parser.add_argument(
        "--resolve-machine-uncertain",
        action="store_true",
        help="revisit gpt-prelabel-v1 uncertain decisions and resolve to keep/reject",
    )
    return parser


def main() -> int:
    args = _arg_parser().parse_args()
    if not 1 <= args.page_size <= 100 or not 1 <= args.batch_size <= 24 or args.workers < 1:
        raise SystemExit("--page-size must be 1..100, --batch-size 1..24, and --workers positive")
    if not 0 <= args.min_confidence <= 1:
        raise SystemExit("--min-confidence must be between 0 and 1")
    settings = Settings(
        review_url=args.review_url,
        openai_base_url=args.openai_base_url,
        model=args.model,
        api_key=_load_api_key(args.auth_file),
        page_size=args.page_size,
        batch_size=args.batch_size,
        workers=args.workers,
        min_confidence=args.min_confidence,
        request_timeout=args.request_timeout,
        max_retries=args.max_retries,
    )
    rows, existing = _load_candidates(
        settings,
        max_samples=0 if args.resolve_machine_uncertain else args.max_samples,
    )
    if args.resolve_machine_uncertain:
        pending = [
            row
            for row in rows
            if (
                existing.get(str(row.get("sample_id")), {}).get("reviewer_id")
                == MACHINE_REVIEWER_ID
                and existing.get(str(row.get("sample_id")), {}).get("review_status")
                == "uncertain"
            )
        ]
        if args.max_samples > 0:
            pending = pending[: args.max_samples]
    else:
        pending = [row for row in rows if str(row.get("sample_id")) not in existing]
    print(
        json.dumps(
            {
                "status": "started",
                "candidate_count": len(rows),
                "already_decided": len(existing),
                "pending": len(pending),
                "model": settings.model,
                "reviewer_id": MACHINE_REVIEWER_ID,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    batches = [
        pending[index : index + settings.batch_size]
        for index in range(0, len(pending), settings.batch_size)
    ]
    completed = 0
    failures = 0
    with ThreadPoolExecutor(max_workers=settings.workers) as executor:
        futures = {
            executor.submit(
                _process_batch,
                batch,
                settings,
                resolve_uncertain=args.resolve_machine_uncertain,
            ): batch
            for batch in batches
        }
        for future in as_completed(futures):
            batch = futures[future]
            try:
                future.result()
                completed += len(batch)
            except Exception as exc:  # noqa: BLE001 - continue a resumable batch
                failures += len(batch)
                print(
                    json.dumps(
                        {
                            "status": "failed",
                            "sample_ids": [row.get("sample_id") for row in batch],
                            "error": str(exc),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                )
            if (completed + failures) % 120 == 0 or completed + failures == len(pending):
                print(
                    json.dumps(
                        {
                            "status": "progress",
                            "processed": completed + failures,
                            "completed": completed,
                            "failed": failures,
                            "total": len(pending),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
    print(
        json.dumps(
            {"status": "finished", "completed": completed, "failed": failures},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
