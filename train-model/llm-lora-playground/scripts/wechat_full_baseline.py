#!/usr/bin/env python3
"""Run a private, hash-only base-model baseline over an experiment snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from llm_lora_playground.tracking import (  # noqa: E402
    finish_training_run,
    log_artifact_with_sha256,
    log_training_metrics,
    start_training_run,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(path: Path, count: int | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            metadata = row.get("metadata", {})
            messages = row.get("messages")
            if not isinstance(messages, list) or not messages or messages[-1].get("role") != "assistant":
                raise ValueError(f"invalid messages at line {line_number}")
            if metadata.get("owner_bulk_approved") is not True:
                raise ValueError(f"owner-bulk approval marker missing at line {line_number}")
            if metadata.get("formal_training_eligible") is not False:
                raise ValueError(f"formal-training marker changed at line {line_number}")
            rows.append(row)
            if count is not None and len(rows) >= count:
                break
    if not rows:
        raise ValueError("dataset is empty")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--count", type=int)
    parser.add_argument("--max-input-tokens", type=int, default=768)
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    data_path = args.data.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    model_path = args.model.expanduser().resolve()
    if args.batch_size <= 0:
        raise ValueError("batch-size must be positive")
    rows = load_rows(data_path, args.count)
    dataset_digest = sha256_file(data_path)
    manifest = {
        "run_kind": "owner_bulk_approved_full_baseline",
        "task": "private_redacted_chat_full_baseline",
        "dataset_manifest_digest": dataset_digest,
        "objective_metric": "generation_success_rate",
        "objective_mode": "max",
        "owner_bulk_approved": True,
        "formal_training_eligible": False,
        "sample_count": len(rows),
        "output_text_persisted": False,
    }
    context = start_training_run(manifest)
    output_dir = output_dir / context.run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        tokenizer.padding_side = "left"
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        model = AutoModelForCausalLM.from_pretrained(
            model_path,
            local_files_only=True,
            dtype=torch.bfloat16,
        ).to("cuda:0")
        model.eval()
        torch.manual_seed(args.seed)
        torch.cuda.manual_seed_all(args.seed)
        records: list[dict[str, Any]] = []
        batch_times: list[float] = []
        generated_counts: list[int] = []
        prompt_counts: list[int] = []
        torch.cuda.reset_peak_memory_stats("cuda:0")
        for offset in range(0, len(rows), args.batch_size):
            batch = rows[offset : offset + args.batch_size]
            conversations = [row["messages"][:-1] for row in batch]
            encoded = tokenizer.apply_chat_template(
                conversations,
                tokenize=True,
                add_generation_prompt=True,
                return_tensors="pt",
                return_dict=True,
                padding=True,
                truncation=True,
                max_length=args.max_input_tokens,
                enable_thinking=False,
            )
            tensors = {key: value.to("cuda:0") for key, value in encoded.items()}
            input_width = int(tensors["input_ids"].shape[-1])
            prompt_lengths = tensors["attention_mask"].sum(dim=1).tolist()
            started = time.perf_counter()
            with torch.inference_mode():
                output_ids = model.generate(
                    **tensors,
                    do_sample=True,
                    temperature=0.7,
                    top_p=0.9,
                    max_new_tokens=args.max_new_tokens,
                    repetition_penalty=1.0,
                    pad_token_id=tokenizer.pad_token_id,
                    eos_token_id=tokenizer.eos_token_id,
                )
            torch.cuda.synchronize("cuda:0")
            elapsed = time.perf_counter() - started
            batch_times.append(elapsed)
            generated = output_ids[:, input_width:]
            for row, ids, prompt_tokens in zip(batch, generated, prompt_lengths, strict=True):
                text = tokenizer.decode(ids, skip_special_tokens=True)
                generated_tokens = len(tokenizer.encode(text, add_special_tokens=False))
                generated_counts.append(generated_tokens)
                prompt_counts.append(int(prompt_tokens))
                records.append(
                    {
                        "sample_id": row["sample_id"],
                        "split": row.get("metadata", {}).get("split"),
                        "prompt_tokens": int(prompt_tokens),
                        "generated_tokens": generated_tokens,
                        "output_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "reference_sha256": row.get("metadata", {}).get("content_sha256"),
                        "status": "ok",
                    }
                )
            completed = min(offset + len(batch), len(rows))
            print(json.dumps({"completed": completed, "total": len(rows), "batch_seconds": round(elapsed, 3)}), flush=True)

        records_path = output_dir / "baseline_records.jsonl"
        records_path.write_text(
            "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
            encoding="utf-8",
        )
        total_seconds = sum(batch_times)
        metrics = {
            "sample_count": float(len(records)),
            "success_count": float(len(records)),
            "failure_count": 0.0,
            "generation_success_rate": 1.0,
            "prompt_tokens_mean": statistics.mean(prompt_counts),
            "generated_tokens_mean": statistics.mean(generated_counts),
            "tokens_per_second": sum(generated_counts) / total_seconds,
            "generation_seconds": total_seconds,
            "peak_gpu_memory_mib": torch.cuda.max_memory_allocated("cuda:0") / 2**20,
        }
        report = {
            "status": "completed",
            "run_id": context.run_id,
            "owner_bulk_approved": True,
            "human_review_completed": False,
            "formal_training_eligible": False,
            "quality_evidence_status": "experimental_only",
            "dataset_sha256": dataset_digest,
            "sample_count": len(rows),
            "batch_size": args.batch_size,
            "model_path": str(model_path),
            "model_index_sha256": sha256_file(model_path / "model.safetensors.index.json"),
            "output_text_persisted": False,
            "metrics": metrics,
            "records_sha256": sha256_file(records_path),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        report_path = output_dir / "baseline_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        log_training_metrics(context, metrics, step=0)
        log_artifact_with_sha256(context, report_path, "baseline")
        log_artifact_with_sha256(context, records_path, "baseline")
        finish_training_run(context, status="FINISHED")
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
        return 0
    except Exception:
        finish_training_run(context, status="FAILED")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
