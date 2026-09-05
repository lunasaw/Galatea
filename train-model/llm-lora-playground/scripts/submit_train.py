#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from llm_lora_playground.config import load_training_config, validate_training_config
from llm_lora_playground.datasets import compute_dataset_digest
from llm_lora_playground.training import train
from llm_lora_playground.tracking import finish_training_run, start_training_run


def run_worker(config, runtime=None, resume_from=None, data=None):
    return train(config, runtime=runtime, resume_from=resume_from, data=data)


def run_driver(config_path: Path, data: Path | None = None, resume_from: str | None = None):
    config = load_training_config(config_path)
    errors = validate_training_config(config)
    if errors:
        raise ValueError("blocked config: " + "; ".join(errors))
    data_path = data.resolve() if data else Path(str(config.values.get("data", {}).get("uri", ""))).resolve()
    if data_path.is_dir():
        data_path = data_path / "dataset.jsonl"
    if not data_path.is_file():
        raise ValueError(f"synthetic dataset file is missing: {data_path}")
    manifest = {
        "run_kind": config.values.get("run_kind", "ray_smoke"),
        "task": config.values.get("task", "synthetic_sft_lora"),
        "config_digest": __import__("llm_lora_playground.config", fromlist=["canonical_training_config_digest"]).canonical_training_config_digest(config),
        "dataset_manifest_digest": compute_dataset_digest(data_path),
        "objective_metric": config.values.get("objective_metric"),
        "objective_mode": config.values.get("objective_mode"),
        "owner_bulk_approved": config.values.get("experiment", {}).get("owner_bulk_approved", False),
        "formal_training_eligible": config.values.get("experiment", {}).get("formal_training_eligible", True),
        "approval_basis": config.values.get("experiment", {}).get("approval_basis"),
    }
    context = start_training_run(manifest)
    try:
        result = run_worker(
            config,
            runtime={"run_id": context.run_id, "tracking_owner": "driver", "tracking_context": context},
            resume_from=resume_from,
            data=data,
        )
    except Exception:
        finish_training_run(context, status="FAILED")
        raise
    finish_training_run(context, status="FINISHED")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--resume-from")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.run:
        run_driver(args.config, args.data, args.resume_from)
    else:
        print({"status": "planned", "config": str(args.config), "run": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
