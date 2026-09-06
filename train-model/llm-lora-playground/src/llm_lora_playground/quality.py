"""Deterministic validation generation metrics without persisting generated text."""

from __future__ import annotations

import hashlib
import math
import statistics
import time
from collections import Counter
from contextlib import nullcontext
from typing import Any, Sequence

from .datasets import TrainingSample


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _f1(left: Sequence[int], right: Sequence[int]) -> float:
    if not left or not right:
        return 0.0
    overlap = sum((Counter(left) & Counter(right)).values())
    precision = overlap / len(left)
    recall = overlap / len(right)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _rouge_l(left: Sequence[int], right: Sequence[int]) -> float:
    if not left or not right:
        return 0.0
    previous = [0] * (len(right) + 1)
    for token in left:
        current = [0]
        for index, other in enumerate(right, 1):
            current.append(previous[index - 1] + 1 if token == other else max(current[-1], previous[index]))
        previous = current
    lcs = previous[-1]
    precision = lcs / len(left)
    recall = lcs / len(right)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _repeated_trigram(tokens: Sequence[int]) -> bool:
    trigrams = [tuple(tokens[index : index + 3]) for index in range(max(len(tokens) - 2, 0))]
    return len(trigrams) != len(set(trigrams))


def _quality_score(metrics: dict[str, float]) -> float:
    overlap = (
        0.45 * metrics["token_f1_mean"]
        + 0.35 * metrics["rouge_l_f1_mean"]
        + 0.10 * metrics["exact_match_rate"]
        + 0.10 * metrics["format_follow_rate"]
    )
    repetition_penalty = 1.0 - 0.25 * metrics["repetition_3gram_rate"]
    truncation_penalty = 1.0 - 0.10 * metrics["max_length_stop_rate"]
    return 100.0 * overlap * repetition_penalty * truncation_penalty


def _input_ids(value: Any) -> list[int]:
    if hasattr(value, "get"):
        candidate = value.get("input_ids")
        if candidate is not None:
            value = candidate
    elif isinstance(value, dict):
        value = value.get("input_ids")
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, list) and value and isinstance(value[0], list):
        value = value[0]
    if not isinstance(value, list) or any(not isinstance(token, int) for token in value):
        raise ValueError("chat template must return integer input_ids")
    return list(value)


def evaluate_generation_quality(
    model: Any,
    tokenizer: Any,
    samples: Sequence[TrainingSample],
    *,
    device: str,
    max_input_tokens: int,
    max_new_tokens: int,
    batch_size: int,
    adapter_enabled: bool,
) -> dict[str, float | str]:
    import torch

    if not samples:
        raise ValueError("validation generation requires non-empty samples")
    context = nullcontext() if adapter_enabled else model.disable_adapter()
    previous_padding_side = tokenizer.padding_side
    tokenizer.padding_side = "left"
    latencies: list[float] = []
    generated_lengths: list[int] = []
    prompt_lengths: list[int] = []
    length_ratios: list[float] = []
    token_f1: list[float] = []
    rouge_l: list[float] = []
    exact: list[float] = []
    non_empty: list[float] = []
    repetitions: list[float] = []
    stopped_at_limit: list[float] = []
    output_digests: list[str] = []
    failures = 0
    started = time.perf_counter()
    torch.cuda.reset_peak_memory_stats(device)
    model.eval()
    try:
        with context, torch.inference_mode():
            for offset in range(0, len(samples), batch_size):
                batch = samples[offset : offset + batch_size]
                prompts: list[list[int]] = []
                references: list[list[int]] = []
                for sample in batch:
                    rendered = tokenizer.apply_chat_template(
                        sample.messages[:-1],
                        tokenize=True,
                        add_generation_prompt=True,
                        truncation=True,
                        max_length=max_input_tokens,
                        enable_thinking=False,
                    )
                    prompt = _input_ids(rendered)
                    prompts.append(prompt)
                    reference = tokenizer(sample.messages[-1]["content"], add_special_tokens=False)["input_ids"]
                    references.append(list(reference))
                width = max(len(prompt) for prompt in prompts)
                input_ids = torch.tensor(
                    [[tokenizer.pad_token_id] * (width - len(prompt)) + prompt for prompt in prompts], device=device
                )
                attention_mask = (input_ids != tokenizer.pad_token_id).long()
                batch_started = time.perf_counter()
                outputs = model.generate(
                    input_ids=input_ids,
                    attention_mask=attention_mask,
                    do_sample=False,
                    max_new_tokens=max_new_tokens,
                    pad_token_id=tokenizer.pad_token_id,
                    eos_token_id=tokenizer.eos_token_id,
                )
                torch.cuda.synchronize(device)
                batch_latency = time.perf_counter() - batch_started
                latencies.append(batch_latency)
                for index, reference in enumerate(references):
                    generated = outputs[index, width:].detach().cpu().tolist()
                    while generated and generated[-1] == tokenizer.pad_token_id:
                        generated.pop()
                    try:
                        decoded = tokenizer.decode(generated, skip_special_tokens=True).strip()
                    except Exception:
                        failures += 1
                        continue
                    generated_lengths.append(len(generated))
                    prompt_lengths.append(len(prompts[index]))
                    length_ratios.append(len(generated) / max(len(reference), 1))
                    token_f1.append(_f1(generated, reference))
                    rouge_l.append(_rouge_l(generated, reference))
                    exact.append(float(generated == reference))
                    non_empty.append(float(bool(decoded)))
                    repetitions.append(float(_repeated_trigram(generated)))
                    stopped_at_limit.append(float(len(generated) >= max_new_tokens))
                    output_digests.append(hashlib.sha256(decoded.encode("utf-8")).hexdigest())
    finally:
        tokenizer.padding_side = previous_padding_side
        model.train()
    wall_seconds = time.perf_counter() - started
    success_count = len(generated_lengths)
    generated_tokens = sum(generated_lengths)
    metrics: dict[str, float | str] = {
        "sample_count": float(len(samples)),
        "success_count": float(success_count),
        "failure_count": float(failures),
        "generation_success_rate": success_count / len(samples),
        "generation_wall_seconds": wall_seconds,
        "generated_tokens": float(generated_tokens),
        "tokens_per_second": generated_tokens / wall_seconds,
        "samples_per_second": success_count / wall_seconds,
        "effective_latency_ms_per_sample": wall_seconds * 1000 / max(success_count, 1),
        "batch_latency_ms_mean": statistics.fmean(latencies) * 1000 if latencies else 0.0,
        "batch_latency_ms_p50": _percentile(latencies, 0.50) * 1000,
        "batch_latency_ms_p95": _percentile(latencies, 0.95) * 1000,
        "generated_tokens_mean": statistics.fmean(generated_lengths) if generated_lengths else 0.0,
        "generated_tokens_p50": _percentile(generated_lengths, 0.50),
        "generated_tokens_p95": _percentile(generated_lengths, 0.95),
        "prompt_tokens_mean": statistics.fmean(prompt_lengths) if prompt_lengths else 0.0,
        "length_ratio_mean": statistics.fmean(length_ratios) if length_ratios else 0.0,
        "token_f1_mean": statistics.fmean(token_f1) if token_f1 else 0.0,
        "rouge_l_f1_mean": statistics.fmean(rouge_l) if rouge_l else 0.0,
        "exact_match_rate": statistics.fmean(exact) if exact else 0.0,
        "format_follow_rate": statistics.fmean(non_empty) if non_empty else 0.0,
        "repetition_3gram_rate": statistics.fmean(repetitions) if repetitions else 0.0,
        "max_length_stop_rate": statistics.fmean(stopped_at_limit) if stopped_at_limit else 0.0,
        "generation_peak_gpu_memory_mib": float(torch.cuda.max_memory_allocated(device) / 2**20),
        "output_digest_set_sha256": hashlib.sha256("".join(output_digests).encode("ascii")).hexdigest(),
    }
    metrics["auxiliary_quality_score"] = _quality_score({key: float(metrics[key]) for key in (
        "token_f1_mean", "rouge_l_f1_mean", "exact_match_rate", "format_follow_rate",
        "repetition_3gram_rate", "max_length_stop_rate",
    )})
    return metrics
