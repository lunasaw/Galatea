"""Tokenizer adapter and fail-closed assistant-only supervision."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import inspect


class LossMaskContractError(ValueError):
    pass


@dataclass(frozen=True)
class TokenizedSample:
    input_ids: list[int]
    labels: list[int]
    attention_mask: list[int]
    assistant_spans: list[tuple[int, int]]


def build_assistant_only_labels(input_ids: list[int], assistant_spans: list[tuple[int, int]], pad_token_id: int | None) -> list[int]:
    if not assistant_spans:
        raise LossMaskContractError("no assistant token span was identified")
    labels = [-100] * len(input_ids)
    for start, end in assistant_spans:
        if start < 0 or end > len(input_ids) or start >= end:
            raise LossMaskContractError("assistant span is outside input_ids")
        for index in range(start, end):
            if pad_token_id is None or input_ids[index] != pad_token_id:
                labels[index] = input_ids[index]
    if not any(label != -100 for label in labels):
        raise LossMaskContractError("assistant spans contain no valid target tokens")
    return labels


def find_assistant_spans(tokenizer: Any, messages: list[dict[str, str]], rendered: Any) -> list[tuple[int, int]]:
    mask = None
    if isinstance(rendered, dict):
        mask = rendered.get("assistant_tokens_mask") or rendered.get("assistant_mask")
    if mask is None:
        return []
    spans: list[tuple[int, int]] = []
    start = None
    for index, flag in enumerate(mask):
        if flag and start is None:
            start = index
        if not flag and start is not None:
            spans.append((start, index))
            start = None
    if start is not None:
        spans.append((start, len(mask)))
    return spans


def tokenize_conversation(tokenizer: Any, messages: list[dict[str, str]], max_length: int, enable_thinking: bool = False) -> TokenizedSample:
    kwargs = {
        "tokenize": True,
        "add_generation_prompt": False,
        "truncation": True,
        "max_length": max_length,
        "enable_thinking": enable_thinking,
    }
    try:
        signature = inspect.signature(tokenizer.apply_chat_template)
        if "return_assistant_tokens_mask" in signature.parameters:
            kwargs["return_assistant_tokens_mask"] = True
    except (TypeError, ValueError):
        pass
    rendered = tokenizer.apply_chat_template(messages, **kwargs)
    if not isinstance(rendered, dict):
        rendered = {"input_ids": rendered}
    input_ids = list(rendered["input_ids"])
    spans = find_assistant_spans(tokenizer, messages, rendered)
    spans = [(start, min(end, len(input_ids))) for start, end in spans if start < len(input_ids)]
    labels = build_assistant_only_labels(input_ids, spans, getattr(tokenizer, "pad_token_id", None))
    return TokenizedSample(input_ids, labels, [1 if token != getattr(tokenizer, "pad_token_id", None) else 0 for token in input_ids], spans)


class SFTCollator:
    def __init__(self, pad_token_id: int):
        self.pad_token_id = pad_token_id

    def __call__(self, samples: list[TokenizedSample]) -> dict[str, list[list[int]]]:
        width = max(len(sample.input_ids) for sample in samples)
        input_ids, labels, attention = [], [], []
        for sample in samples:
            pad = width - len(sample.input_ids)
            input_ids.append(sample.input_ids + [self.pad_token_id] * pad)
            labels.append(sample.labels + [-100] * pad)
            attention.append(sample.attention_mask + [0] * pad)
        return {"input_ids": input_ids, "labels": labels, "attention_mask": attention}
