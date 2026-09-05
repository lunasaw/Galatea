"""Small, parameterized SFT+LoRA runner used by local and Ray entrypoints.

The implementation deliberately imports heavyweight libraries inside ``train``.  This
keeps configuration, schema, and unit-test paths usable on a service-only host while
making a real run fail closed when the project environment or GPU contract is absent.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .checkpoints import CheckpointManifest, checkpoint_record, save_checkpoint
from .config import ProjectConfig, canonical_training_config_digest, validate_training_config
from .datasets import compute_dataset_digest, load_samples
from .lora import build_lora_model
from .runtime import collect_environment_snapshot
from .sft import tokenize_conversation
from .tracking import finish_training_run, start_training_run, log_training_metrics, log_artifact_with_sha256


class TrainingContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class TrainResult:
    run_id: str
    attempt_id: str
    status: str
    steps: int
    metrics: dict[str, float]
    checkpoint: CheckpointManifest | None
    manifest: dict[str, Any]


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except ImportError:
        pass


def _resolve_data_path(config: ProjectConfig, data: Path | None) -> Path:
    if data is not None:
        candidate = data.expanduser().resolve()
        if candidate.is_dir():
            candidate = candidate / "dataset.jsonl"
        return candidate
    return Path(str(config.values.get("data", {}).get("uri", ""))).expanduser().resolve()


def _require_gpu(device: str) -> None:
    try:
        import torch
    except ImportError as exc:
        raise TrainingContractError("torch is required for --run; install the project environment") from exc
    if not torch.cuda.is_available():
        raise TrainingContractError("cuda is unavailable; refusing to downgrade the declared single-GPU run")
    if device != "cuda:0":
        raise TrainingContractError(f"declared device must remain cuda:0, got {device}")


def train(config: ProjectConfig, runtime: dict[str, Any] | None = None, resume_from: str | None = None, data: Path | None = None) -> TrainResult:
    errors = validate_training_config(config)
    if errors:
        raise TrainingContractError("blocked config: " + "; ".join(errors))
    _require_gpu(str(config.values["model"]["device"]))
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise TrainingContractError("torch and transformers are required for --run") from exc

    data_path = _resolve_data_path(config, data)
    if not data_path.is_file() or data_path.is_symlink():
        raise TrainingContractError(f"synthetic dataset file is missing or unsafe: {data_path}")
    samples = list(load_samples(data_path))
    if not samples:
        raise TrainingContractError("synthetic dataset is empty")
    config_digest = canonical_training_config_digest(config)
    dataset_digest = compute_dataset_digest(data_path)
    model_cfg = config.values["model"]
    model_path = Path(str(model_cfg["local_path"])).expanduser()
    if not model_path.is_dir():
        raise TrainingContractError(f"model path is missing: {model_path}")
    seed = int(config.values["training"].get("seed", 42))
    _seed_everything(seed)
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True, trust_remote_code=bool(model_cfg.get("trust_remote_code", False)))
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    base_model = AutoModelForCausalLM.from_pretrained(
        model_path,
        local_files_only=True,
        torch_dtype=torch.bfloat16,
        trust_remote_code=bool(model_cfg.get("trust_remote_code", False)),
    ).to(model_cfg["device"])
    resume_manifest_path: Path | None = None
    resume_step = 0
    if resume_from:
        resume_manifest_path = Path(resume_from).expanduser().resolve()
        if resume_manifest_path.is_dir():
            resume_manifest_path = resume_manifest_path / "checkpoint_manifest.json"
        if not resume_manifest_path.is_file():
            raise TrainingContractError(f"resume checkpoint manifest is missing: {resume_manifest_path}")
        resume_payload = json.loads(resume_manifest_path.read_text(encoding="utf-8"))
        identity = resume_payload.get("metadata", {})
        resume_step = int(resume_payload.get("step", 0))
        if resume_payload.get("status") != "complete":
            raise TrainingContractError("resume checkpoint is not complete")
        if identity.get("config_digest") not in {None, config_digest} or identity.get("dataset_manifest_digest") not in {None, dataset_digest}:
            raise TrainingContractError("resume checkpoint identity does not match current config/data")
        try:
            from peft import PeftModel

            adapter_dir = resume_manifest_path.parent.parent / "adapter"
            model = PeftModel.from_pretrained(base_model, str(adapter_dir), is_trainable=True)
        except ImportError as exc:
            raise TrainingContractError("PEFT is required to resume an adapter checkpoint") from exc
        except Exception as exc:
            raise TrainingContractError(f"could not load resume adapter: {type(exc).__name__}: {exc}") from exc
    else:
        model = build_lora_model(base_model, config.values["lora"])
    model.train()
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(config.values["training"]["learning_rate"]))
    encoded = [tokenize_conversation(tokenizer, sample.messages, int(model_cfg["max_sequence_length"]), bool(model_cfg.get("enable_thinking", False))) for sample in samples]
    batch_size = int(config.values["training"].get("per_device_train_batch_size", 1))
    max_steps = config.values["training"].get("max_steps")
    epochs = int(config.values["training"].get("epochs", 1))
    if max_steps is None:
        max_steps = max(1, (len(encoded) * epochs + batch_size - 1) // batch_size)
    configured_output_root = config.values.get("output_root")
    output_root = Path(str((runtime or {}).get("output_root", configured_output_root or "platform-data/llm-baselines/toy-lora"))).expanduser().resolve()
    run_id = str((runtime or {}).get("run_id", f"local-{int(time.time())}"))
    attempt_id = str((runtime or {}).get("attempt_id", f"attempt-{run_id}"))
    tracking_context = (runtime or {}).get("tracking_context")
    tracking_uri = (runtime or {}).get("tracking_uri") or os.environ.get("MLFLOW_TRACKING_URI")
    experiment_name = (runtime or {}).get("experiment_name") or os.environ.get("MLFLOW_EXPERIMENT_NAME")
    if tracking_uri and experiment_name and tracking_context is None and (runtime or {}).get("tracking_owner") != "driver":
        manifest_preview = {
            "run_id": run_id,
            "run_kind": config.values.get("run_kind", "training"),
            "task": config.values.get("task", "synthetic_sft_lora"),
            "config_digest": config_digest,
            "dataset_manifest_digest": dataset_digest,
            "objective_metric": config.values.get("objective_metric"),
            "objective_mode": config.values.get("objective_mode"),
            "seed": seed,
            "resources": config.values.get("resources", {}),
            "owner_bulk_approved": config.values.get("experiment", {}).get("owner_bulk_approved", False),
            "formal_training_eligible": config.values.get("experiment", {}).get("formal_training_eligible", True),
            "approval_basis": config.values.get("experiment", {}).get("approval_basis"),
        }
        try:
            tracking_context = start_training_run(manifest_preview, tracking_uri, experiment_name)
            run_id = tracking_context.run_id
        except Exception as exc:
            raise TrainingContractError(f"MLflow parent Run could not be created through the API: {type(exc).__name__}: {exc}") from exc
    losses: list[float] = []
    checkpoint: CheckpointManifest | None = None
    step = resume_step
    try:
        while step < int(max_steps):
            for offset in range(0, len(encoded), batch_size):
                if step >= int(max_steps):
                    break
                batch = encoded[offset : offset + batch_size]
                width = max(len(item.input_ids) for item in batch)
                input_ids = torch.tensor([item.input_ids + [tokenizer.pad_token_id] * (width - len(item.input_ids)) for item in batch], device=model_cfg["device"])
                labels = torch.tensor([item.labels + [-100] * (width - len(item.labels)) for item in batch], device=model_cfg["device"])
                attention_mask = (input_ids != tokenizer.pad_token_id).long()
                optimizer.zero_grad(set_to_none=True)
                output = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
                loss = output.loss
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), float(config.values["training"].get("max_grad_norm", 1.0)))
                optimizer.step()
                step += 1
                losses.append(float(loss.detach().cpu()))
                if tracking_context:
                    log_training_metrics(tracking_context, {"train_loss": losses[-1]}, tracking_uri, step=step)
                save_steps = int(config.values["training"].get("save_steps", 25))
                if step % save_steps == 0 or step == int(max_steps):
                    attempt_dir = output_root / run_id / attempt_id
                    adapter_dir = attempt_dir / f"step-{step}" / "adapter"
                    adapter_dir.mkdir(parents=True, exist_ok=True)
                    model.save_pretrained(adapter_dir, safe_serialization=True)
                    tokenizer.save_pretrained(adapter_dir)
                    state = {path.name: path.read_bytes() for path in adapter_dir.iterdir() if path.is_file()}
                    optimizer_buffer = io.BytesIO()
                    torch.save(optimizer.state_dict(), optimizer_buffer)
                    state["optimizer.pt"] = optimizer_buffer.getvalue()
                    rng_buffer = io.BytesIO()
                    torch.save({"torch": torch.get_rng_state(), "python": random.getstate()}, rng_buffer)
                    state["rng_state.pt"] = rng_buffer.getvalue()
                    checkpoint = save_checkpoint(
                        state,
                        output_root / run_id / "checkpoints",
                        {
                            "step": step,
                            "attempt_id": attempt_id,
                            "config_digest": config_digest,
                            "dataset_manifest_digest": dataset_digest,
                            "seed": seed,
                            "model_revision": str(model_cfg.get("revision") or model_cfg["id"]),
                            "environment_digest": hashlib.sha256(json.dumps(collect_environment_snapshot(), sort_keys=True, default=str).encode()).hexdigest(),
                        },
                    )
    except KeyboardInterrupt:
        if tracking_context:
            finish_training_run(tracking_context, status="KILLED", tracking_uri=tracking_uri)
        return TrainResult(run_id, attempt_id, "interrupted", step, {"train_loss": sum(losses) / len(losses) if losses else 0.0}, checkpoint, {"status": "interrupted"})
    metrics = {"train_loss": sum(losses) / len(losses) if losses else 0.0, "steps": float(step), "sample_count": float(len(samples))}
    manifest = {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "status": "completed",
        "config_digest": config_digest,
        "dataset_manifest_digest": dataset_digest,
        "seed": seed,
        "metrics": metrics,
        "checkpoint": checkpoint_record(checkpoint) if checkpoint else None,
    }
    manifest_path = output_root / run_id / attempt_id / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if tracking_context:
        try:
            log_artifact_with_sha256(tracking_context, manifest_path, "manifests", tracking_uri)
            log_training_metrics(tracking_context, metrics, tracking_uri, step=step)
        except Exception as exc:
            raise TrainingContractError(f"MLflow artifact/metric logging failed: {type(exc).__name__}: {exc}") from exc
        finish_training_run(tracking_context, status="FINISHED", tracking_uri=tracking_uri)
    return TrainResult(run_id, attempt_id, "completed", step, metrics, checkpoint, manifest)
