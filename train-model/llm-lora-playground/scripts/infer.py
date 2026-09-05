#!/usr/bin/env python3
"""Project-0+1 Qwen3.5 baseline CLI; safe to submit as a Ray Job."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from llm_lora_playground.config import canonical_config_digest, env_or_config, load_config, validate_config
from llm_lora_playground.data import DatasetExpectation, build_validation_fixtures, fixture_digest
from llm_lora_playground.data_delivery import check_data_delivery, resolve_dataset_root
from llm_lora_playground.models.causal_lm import ModelConfig, prepare_inputs, load_model_and_tokenizer, generate_one, GenerationConfig
from llm_lora_playground.runtime import check_gpu_capabilities, collect_environment_snapshot


def _paths(args: argparse.Namespace) -> tuple[Path, Path]:
    config_path = args.config.resolve()
    data_path = args.data_config.resolve() if args.data_config else PROJECT_ROOT / "configs/data.yaml"
    return config_path, data_path


def _data_config(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _resolved_root(data_config: dict) -> Path:
    dataset = data_config.get("dataset", {})
    explicit = os.environ.get(str(dataset.get("root_env", "WECHAT_DATA_ROOT")))
    return resolve_dataset_root(Path(str(dataset.get("root", "/data/ai/chenzhangyue/code/data"))), Path(explicit) if explicit else None)


def _model_config(config) -> ModelConfig:
    model = config.values["model"]
    return ModelConfig(model_id=model["id"], local_path=os.environ.get("QWEN35_MODEL_PATH", model["local_path"]), dtype=model["dtype"], device=model["device"], max_input_tokens=int(model["max_input_tokens"]), trust_remote_code=bool(model.get("trust_remote_code", False)), enable_thinking=bool(model["enable_thinking"]))


def check_config(config_path: Path, data_path: Path) -> dict:
    config = load_config(config_path)
    errors = validate_config(config)
    return {"status": "ok" if not errors else "blocked", "errors": errors, "config_digest": canonical_config_digest(config), "will_create_mlflow_run": False, "config": str(config_path), "data_config": str(data_path)}


def run_smoke(config_path: Path, data_path: Path) -> dict:
    config = load_config(config_path)
    errors = validate_config(config)
    if errors:
        return {"status": "blocked", "errors": errors, "will_create_mlflow_run": False}
    gpu = check_gpu_capabilities(config.values["model"]["device"])
    environment = collect_environment_snapshot()
    if gpu["status"] != "ok":
        return {"status": "blocked", "gpu": gpu, "environment": environment, "will_create_mlflow_run": False}
    try:
        loaded = load_model_and_tokenizer(_model_config(config))
        generation = GenerationConfig(**{key: value for key, value in config.values["generation"].items() if key in GenerationConfig.__dataclass_fields__})
        prompts = [[{"role": "user", "content": "用一句话解释什么是机器学习。"}], [{"role": "user", "content": "给出一个简短的健康早餐建议。"}]]
        results = []
        for messages in prompts:
            inputs = prepare_inputs(loaded.tokenizer, messages, False, _model_config(config).max_input_tokens)
            results.append(generate_one(loaded, inputs, generation).__dict__)
        return {"status": "ok", "model_revision": loaded.resolved_revision, "gpu": gpu, "environment": environment, "smoke_results": results, "will_create_mlflow_run": False}
    except Exception as exc:
        return {"status": "blocked", "error": f"{type(exc).__name__}: {exc}", "gpu": gpu, "environment": environment, "will_create_mlflow_run": False}


def plan_data(config_path: Path, data_path: Path, output_dir: Path) -> dict:
    config = load_config(config_path)
    data = _data_config(data_path)
    ds = data["dataset"]
    expectation = DatasetExpectation(ds["dataset_id"], ds["source_sha256"], ds["config_sha256"], ds["pipeline_version"])
    root = _resolved_root(data)
    preflight = check_data_delivery(root, expectation)
    result = {"status": preflight.status, "root": preflight.root, "blocked_reasons": preflight.blocked_reasons, "identity": preflight.identity.__dict__ if preflight.identity else None}
    if preflight.identity and not preflight.blocked_reasons:
        fixtures = build_validation_fixtures(root, data["split"]["selected_split"], int(data["split"]["fixture_count"]))
        result["fixture_count"] = len(fixtures)
        result["fixture_digest"] = fixture_digest(fixtures)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "data-preflight.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-config", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("platform-data/llm-baselines"))
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--check-data", action="store_true")
    parser.add_argument("--plan-fixtures", action="store_true")
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    config_path, data_path = _paths(args)
    if args.check_config:
        result = check_config(config_path, data_path)
    elif args.check_data or args.plan_fixtures:
        result = plan_data(config_path, data_path, args.output_dir)
        if args.plan_fixtures and result.get("status") == "ok":
            result["mode"] = "plan-fixtures"
    elif args.smoke_only:
        result = run_smoke(config_path, data_path)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "smoke-report.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
    elif args.run:
        result = {"status": "blocked", "reason": "20-fixture MLflow run is gated by data authorization and requires a verified consent ledger; use --smoke-only for project-0+1 model smoke", "will_create_mlflow_run": False}
    else:
        parser.error("one of --check-config, --check-data, --plan-fixtures, --smoke-only, --run is required")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if result.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
