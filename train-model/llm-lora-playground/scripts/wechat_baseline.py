#!/usr/bin/env python3
"""Run a private, inference-only baseline from the copied WeChat dataset.

The command intentionally bypasses formal SFT and MLflow gates: it only measures
base-model generation on a bounded sample of the copied, provisional all-keep
baseline.  It records hashes and timing, not generated private text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from statistics import mean, median
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from llm_lora_playground.config import load_config, validate_config  # noqa: E402
from llm_lora_playground.models.causal_lm import (  # noqa: E402
    GenerationConfig,
    ModelConfig,
    generate_one,
    load_model_and_tokenizer,
    prepare_inputs,
)
from llm_lora_playground.runtime import check_gpu_capabilities, collect_environment_snapshot  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(path: Path, count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            messages = row.get("messages")
            if not isinstance(messages, list) or len(messages) < 2 or messages[-1].get("role") != "assistant":
                raise ValueError(f"invalid baseline row at line {line_number}")
            if row.get("metadata", {}).get("baseline_only") is not True:
                raise ValueError("baseline row is missing baseline_only marker")
            rows.append(row)
            if len(rows) >= count:
                break
    if not rows:
        raise ValueError(f"no rows found in {path}")
    return rows


def model_config(config: Any) -> ModelConfig:
    model = config.values["model"]
    return ModelConfig(
        model_id=model["id"],
        local_path=os.environ.get("QWEN35_MODEL_PATH", model["local_path"]),
        dtype=model["dtype"],
        device=model["device"],
        max_input_tokens=int(model["max_input_tokens"]),
        trust_remote_code=bool(model.get("trust_remote_code", False)),
        enable_thinking=bool(model["enable_thinking"]),
    )


def run_baseline(config_path: Path, baseline_root: Path, output_dir: Path, count: int) -> dict[str, Any]:
    config = load_config(config_path)
    errors = validate_config(config)
    if errors:
        return {"status": "blocked", "errors": errors, "baseline_only": True}
    baseline_root = baseline_root.expanduser().resolve()
    manifest_path = baseline_root / "baseline_manifest.json"
    validation_path = baseline_root / "datasets/validation.jsonl"
    if not manifest_path.is_file() or not validation_path.is_file():
        return {"status": "blocked", "baseline_only": True, "reason": "baseline copy is incomplete"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("baseline_only") is not True or manifest.get("formal_training_eligible") is not False:
        return {"status": "blocked", "baseline_only": True, "reason": "baseline markers are invalid"}
    rows = load_rows(validation_path, count)
    gpu = check_gpu_capabilities(config.values["model"]["device"])
    environment = collect_environment_snapshot()
    if gpu["status"] != "ok":
        return {"status": "blocked", "baseline_only": True, "gpu": gpu, "environment": environment}
    try:
        loaded = load_model_and_tokenizer(model_config(config))
        generation = GenerationConfig(**{key: value for key, value in config.values["generation"].items() if key in GenerationConfig.__dataclass_fields__})
        records: list[dict[str, Any]] = []
        for row in rows:
            messages = row["messages"][:-1]
            started = time.perf_counter()
            inputs = prepare_inputs(loaded.tokenizer, messages, False, model_config(config).max_input_tokens)
            result = generate_one(loaded, inputs, generation)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            records.append(
                {
                    "sample_id": row["sample_id"],
                    "session_id": row.get("session_id"),
                    "prompt_tokens": result.prompt_tokens,
                    "generated_tokens": result.generated_tokens,
                    "latency_ms": result.total_latency_ms,
                    "end_to_end_ms": elapsed_ms,
                    "tokens_per_second": result.tokens_per_second,
                    "peak_gpu_memory_mib": result.peak_gpu_memory_mib,
                    "output_sha256": result.output_sha256,
                    "reference_sha256": row["metadata"].get("content_sha256"),
                    "reference_chars": len(row["messages"][-1].get("content", "")),
                    "status": result.status,
                    "error": result.error,
                }
            )
        ok = [item for item in records if item["status"] == "ok"]
        metrics = {
            "sample_count": len(records),
            "success_count": len(ok),
            "failure_count": len(records) - len(ok),
            "prompt_tokens_mean": mean(item["prompt_tokens"] for item in ok) if ok else 0.0,
            "generated_tokens_mean": mean(item["generated_tokens"] for item in ok) if ok else 0.0,
            "latency_ms_mean": mean(item["latency_ms"] for item in ok) if ok else 0.0,
            "latency_ms_p50": median(item["latency_ms"] for item in ok) if ok else 0.0,
            "tokens_per_second_mean": mean(item["tokens_per_second"] for item in ok) if ok else 0.0,
            "peak_gpu_memory_mib_max": max((item["peak_gpu_memory_mib"] for item in ok), default=0.0),
        }
        output_dir = output_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "baseline_records.jsonl").write_text(
            "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in records),
            encoding="utf-8",
        )
        report = {
            "status": "ok" if len(ok) == len(records) else "partial",
            "baseline_only": True,
            "formal_training_eligible": False,
            "data_manifest": str(manifest_path),
            "validation_file_sha256": sha256_file(validation_path),
            "model_revision": loaded.resolved_revision,
            "gpu": gpu,
            "environment": environment,
            "metrics": metrics,
            "output_text_persisted": False,
            "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        (output_dir / "baseline_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return report
    except Exception as exc:
        return {"status": "blocked", "baseline_only": True, "formal_training_eligible": False, "error": f"{type(exc).__name__}: {exc}", "gpu": gpu, "environment": environment}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "configs/inference.yaml")
    parser.add_argument("--baseline-root", type=Path, default=Path("platform-data/llm-private/wechat-review-baseline/wechat_aa807aaad90dc4463964/baseline"))
    parser.add_argument("--output-dir", type=Path, default=Path("platform-data/llm-baselines/wechat-baseline"))
    parser.add_argument("--count", type=int, default=20)
    args = parser.parse_args()
    result = run_baseline(args.config, args.baseline_root, args.output_dir, args.count)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if result.get("status") in {"ok", "partial"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
