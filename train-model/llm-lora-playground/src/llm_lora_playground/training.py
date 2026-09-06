"""Governed single-worker Ray SFT+LoRA training implementation."""

from __future__ import annotations

import io
import json
import math
import os
import random
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .checkpoints import CheckpointManifest, checkpoint_record, load_checkpoint, save_checkpoint
from .config import ProjectConfig, canonical_training_config_digest, validate_training_config
from .datasets import TrainingSample, compute_dataset_digest, load_samples, partition_samples
from .job_metadata import update_checkpoint_pointer, write_job_metadata_atomic
from .lora import build_lora_model
from .planning import resolve_data_path
from .quality import evaluate_generation_quality
from .runtime import environment_digest
from .sft import TokenizedSample, tokenize_conversation
from .evaluation import EvaluationProtocolError, claim_test_evaluation
from .tracking import log_artifact_directory, log_artifact_with_sha256, log_training_metrics, set_training_tags


class TrainingContractError(RuntimeError):
    pass


REQUIRED_PERFORMANCE_METRICS = frozenset(
    {
        "data_preparation_wall_seconds",
        "model_load_wall_seconds",
        "tokenization_wall_seconds",
        "training_wall_seconds",
        "optimization_compute_wall_seconds",
        "validation_wall_seconds",
        "checkpoint_wall_seconds",
        "checkpoint_upload_wall_seconds",
        "worker_wall_seconds",
        "train_samples_per_second",
        "train_target_tokens_per_second",
        "optimizer_steps_per_second",
        "validation_target_tokens_per_second",
        "training_peak_gpu_memory_mib",
        "training_peak_gpu_reserved_mib",
        "process_max_rss_mib",
        "base_generation_tokens_per_second",
        "base_generation_samples_per_second",
        "base_generation_batch_latency_ms_p50",
        "base_generation_batch_latency_ms_p95",
        "base_generation_generated_tokens_mean",
        "base_generation_generated_tokens_p50",
        "base_generation_generated_tokens_p95",
        "base_generation_generation_peak_gpu_memory_mib",
        "lora_generation_tokens_per_second",
        "lora_generation_samples_per_second",
        "lora_generation_batch_latency_ms_p50",
        "lora_generation_batch_latency_ms_p95",
        "lora_generation_generated_tokens_mean",
        "lora_generation_generated_tokens_p50",
        "lora_generation_generated_tokens_p95",
        "lora_generation_generation_peak_gpu_memory_mib",
    }
)

REQUIRED_MODEL_QUALITY_METRICS = frozenset(
    {
        "train_loss",
        "validation_loss",
        "validation_perplexity",
        "base_validation_loss",
        "base_validation_perplexity",
        "validation_loss_improvement",
        "base_generation_generation_success_rate",
        "base_generation_token_f1_mean",
        "base_generation_rouge_l_f1_mean",
        "base_generation_exact_match_rate",
        "base_generation_format_follow_rate",
        "base_generation_repetition_3gram_rate",
        "base_generation_max_length_stop_rate",
        "base_generation_auxiliary_quality_score",
        "lora_generation_generation_success_rate",
        "lora_generation_token_f1_mean",
        "lora_generation_rouge_l_f1_mean",
        "lora_generation_exact_match_rate",
        "lora_generation_format_follow_rate",
        "lora_generation_repetition_3gram_rate",
        "lora_generation_max_length_stop_rate",
        "lora_generation_auxiliary_quality_score",
        "auxiliary_quality_score_improvement",
    }
)


def validate_required_evidence_metrics(metrics: dict[str, float]) -> None:
    """Fail a governed Run before success if performance or quality evidence is incomplete."""
    required = REQUIRED_PERFORMANCE_METRICS | REQUIRED_MODEL_QUALITY_METRICS
    missing = sorted(required - set(metrics))
    non_finite = sorted(
        key for key in required & set(metrics) if not math.isfinite(float(metrics[key]))
    )
    if missing or non_finite:
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if non_finite:
            details.append("non_finite=" + ",".join(non_finite))
        raise TrainingContractError("incomplete governed metric evidence: " + "; ".join(details))


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


def _require_gpu(device: str) -> None:
    try:
        import torch
    except ImportError as exc:
        raise TrainingContractError("torch is required in the project Ray environment") from exc
    if not torch.cuda.is_available():
        raise TrainingContractError("cuda is unavailable; refusing to downgrade the declared single-GPU run")
    if device != "cuda:0":
        raise TrainingContractError(f"declared device must remain cuda:0, got {device}")


def _encode_samples(
    tokenizer: Any,
    samples: Sequence[TrainingSample],
    max_length: int,
    enable_thinking: bool,
) -> list[TokenizedSample]:
    return [
        tokenize_conversation(tokenizer, sample.messages, max_length, enable_thinking)
        for sample in samples
    ]


def _batch_tensors(batch: Sequence[TokenizedSample], tokenizer: Any, device: str) -> tuple[Any, Any, Any, int]:
    import torch

    width = max(len(item.input_ids) for item in batch)
    input_ids = torch.tensor(
        [item.input_ids + [tokenizer.pad_token_id] * (width - len(item.input_ids)) for item in batch],
        device=device,
    )
    labels = torch.tensor(
        [item.labels + [-100] * (width - len(item.labels)) for item in batch],
        device=device,
    )
    attention_mask = torch.tensor(
        [item.attention_mask + [0] * (width - len(item.attention_mask)) for item in batch],
        device=device,
    )
    target_tokens = int((labels != -100).sum().item())
    return input_ids, labels, attention_mask, target_tokens


def _build_update_groups(
    sample_count: int,
    epochs: int,
    batch_size: int,
    accumulation_steps: int,
    seed: int,
) -> list[list[list[int]]]:
    groups: list[list[list[int]]] = []
    for epoch in range(epochs):
        indices = list(range(sample_count))
        random.Random(seed + epoch).shuffle(indices)
        micro_batches = [indices[index : index + batch_size] for index in range(0, sample_count, batch_size)]
        groups.extend(
            micro_batches[index : index + accumulation_steps]
            for index in range(0, len(micro_batches), accumulation_steps)
        )
    return groups


def _evaluate_validation_loss(
    model: Any,
    encoded: Sequence[TokenizedSample],
    tokenizer: Any,
    device: str,
    batch_size: int,
) -> tuple[float, int, float]:
    import torch

    model.eval()
    weighted_loss = 0.0
    target_tokens = 0
    started = time.perf_counter()
    with torch.inference_mode():
        for offset in range(0, len(encoded), batch_size):
            batch = encoded[offset : offset + batch_size]
            input_ids, labels, attention_mask, batch_targets = _batch_tensors(batch, tokenizer, device)
            output = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
            weighted_loss += float(output.loss.detach().cpu()) * batch_targets
            target_tokens += batch_targets
    model.train()
    if target_tokens == 0:
        raise TrainingContractError("validation split contains no assistant target tokens")
    return weighted_loss / target_tokens, target_tokens, time.perf_counter() - started


def _adapter_state(model: Any, tokenizer: Any) -> dict[str, bytes]:
    state: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory(prefix="llm-lora-adapter-") as directory:
        adapter_dir = Path(directory) / "adapter"
        model.save_pretrained(adapter_dir, safe_serialization=True)
        # Persist the concrete model-tree contract alongside PEFT metadata.
        # ``base_model_name_or_path`` alone cannot distinguish Qwen3.5 text
        # and conditional-generation module prefixes.
        adapter_config_path = adapter_dir / "adapter_config.json"
        if adapter_config_path.is_file():
            adapter_config = json.loads(adapter_config_path.read_text(encoding="utf-8"))
            adapter_config["model_architecture"] = "qwen3_5_conditional_generation"
            adapter_config_path.write_text(
                json.dumps(adapter_config, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
        for path in sorted(adapter_dir.rglob("*")):
            if path.is_file():
                state[f"adapter/{path.relative_to(adapter_dir).as_posix()}"] = path.read_bytes()
    return state


def _trainer_state_bytes(
    optimizer: Any,
    scheduler: Any,
    completed_steps: int,
    torch_module: Any,
) -> bytes:
    buffer = io.BytesIO()
    cuda_rng = torch_module.cuda.get_rng_state_all() if torch_module.cuda.is_available() else []
    torch_module.save(
        {
            "completed_steps": completed_steps,
            "optimizer": optimizer.state_dict(),
            "scheduler": scheduler.state_dict(),
            "python_rng": random.getstate(),
            "torch_rng": torch_module.get_rng_state(),
            "cuda_rng": cuda_rng,
        },
        buffer,
    )
    return buffer.getvalue()


def _restore_trainer_state(path: Path, optimizer: Any, scheduler: Any, torch_module: Any) -> int:
    state = torch_module.load(path, map_location="cpu", weights_only=False)
    optimizer.load_state_dict(state["optimizer"])
    scheduler.load_state_dict(state["scheduler"])
    random.setstate(state["python_rng"])
    torch_module.set_rng_state(state["torch_rng"])
    if state.get("cuda_rng") and torch_module.cuda.is_available():
        torch_module.cuda.set_rng_state_all(state["cuda_rng"])
    return int(state["completed_steps"])


def _save_training_checkpoint(
    model: Any,
    tokenizer: Any,
    optimizer: Any,
    scheduler: Any,
    torch_module: Any,
    output_root: Path,
    run_id: str,
    attempt_id: str,
    step: int,
    metadata: dict[str, Any],
) -> CheckpointManifest:
    state = _adapter_state(model, tokenizer)
    state["trainer_state.pt"] = _trainer_state_bytes(optimizer, scheduler, step, torch_module)
    return save_checkpoint(
        state,
        output_root / run_id / "checkpoints",
        {"step": step, "attempt_id": attempt_id, **metadata},
    )


def train(
    config: ProjectConfig,
    runtime: dict[str, Any] | None = None,
    resume_from: str | None = None,
    data: Path | None = None,
) -> TrainResult:
    worker_started = time.perf_counter()
    runtime = runtime or {}
    if str(runtime.get("execution_mode", "")) != "governed-ray-job":
        raise TrainingContractError("training requires the Galatea-governed Ray Job execution path")
    if not runtime.get("ray_job_id"):
        raise TrainingContractError("Ray training requires ray_job_id")
    if runtime.get("tracking_owner") != "driver" or runtime.get("tracking_context") is None:
        raise TrainingContractError("the Ray Driver must own and provide the parent MLflow Run")
    errors = validate_training_config(config)
    if errors:
        raise TrainingContractError("blocked config: " + "; ".join(errors))
    device = str(config.values["model"]["device"])
    _require_gpu(device)
    try:
        import torch
        from transformers import AutoTokenizer, Qwen3_5ForConditionalGeneration, get_cosine_schedule_with_warmup
    except ImportError as exc:
        raise TrainingContractError("torch and transformers are required in the project Ray environment") from exc

    data_started = time.perf_counter()
    data_path = resolve_data_path(config, data)
    if not data_path.is_file() or data_path.is_symlink():
        raise TrainingContractError(f"dataset file is missing or unsafe: {data_path}")
    samples = list(load_samples(data_path))
    splits = partition_samples(samples, config.values["data"])
    dataset_digest = compute_dataset_digest(data_path)
    declared_dataset_digest = config.values["data"].get("content_sha256")
    if declared_dataset_digest and declared_dataset_digest != dataset_digest:
        raise TrainingContractError("immutable dataset digest changed after planning")
    declared_split_digest = config.values["data"].get("split_sha256")
    if declared_split_digest and declared_split_digest != splits.digest:
        raise TrainingContractError("immutable split digest changed after planning")
    train_samples = splits.samples_by_split["train"]
    validation_samples = splits.samples_by_split["validation"]
    data_preparation_seconds = time.perf_counter() - data_started
    config_digest = canonical_training_config_digest(config)
    model_cfg = config.values["model"]
    if model_cfg.get("architecture") != "qwen3_5_conditional_generation":
        raise TrainingContractError("training requires model.architecture=qwen3_5_conditional_generation")
    training_cfg = config.values["training"]
    model_path = Path(str(model_cfg["local_path"])).expanduser().resolve()
    if not model_path.is_dir():
        raise TrainingContractError(f"model path is missing: {model_path}")
    seed = int(training_cfg["seed"])
    _seed_everything(seed)
    model_load_started = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(
        model_path,
        local_files_only=True,
        trust_remote_code=bool(model_cfg.get("trust_remote_code", False)),
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    base_model = Qwen3_5ForConditionalGeneration.from_pretrained(
        model_path,
        local_files_only=True,
        dtype=torch.bfloat16,
        trust_remote_code=bool(model_cfg.get("trust_remote_code", False)),
    ).to(device)

    resume_manifest: CheckpointManifest | None = None
    if resume_from:
        resume_manifest = load_checkpoint(Path(resume_from).expanduser().resolve())
        identity = resume_manifest.metadata
        if identity.get("config_digest") != config_digest:
            raise TrainingContractError("resume checkpoint config identity does not match")
        if identity.get("dataset_manifest_digest") != dataset_digest:
            raise TrainingContractError("resume checkpoint dataset identity does not match")
        if identity.get("split_digest") != splits.digest:
            raise TrainingContractError("resume checkpoint split identity does not match")
        try:
            from peft import PeftModel

            model = PeftModel.from_pretrained(base_model, str(resume_manifest.path / "adapter"), is_trainable=True)
        except Exception as exc:
            raise TrainingContractError(f"could not load resume adapter: {type(exc).__name__}: {exc}") from exc
    else:
        model = build_lora_model(base_model, config.values["lora"])
    model.train()
    model_load_seconds = time.perf_counter() - model_load_started

    tokenization_started = time.perf_counter()
    max_length = int(model_cfg["max_sequence_length"])
    enable_thinking = bool(model_cfg.get("enable_thinking", False))
    encoded_train = _encode_samples(tokenizer, train_samples, max_length, enable_thinking)
    encoded_validation = _encode_samples(tokenizer, validation_samples, max_length, enable_thinking)
    tokenization_seconds = time.perf_counter() - tokenization_started
    batch_size = int(training_cfg["per_device_train_batch_size"])
    eval_batch_size = int(training_cfg.get("per_device_eval_batch_size", batch_size))
    accumulation_steps = int(training_cfg["gradient_accumulation_steps"])
    update_groups = _build_update_groups(
        len(encoded_train), int(training_cfg["epochs"]), batch_size, accumulation_steps, seed
    )
    if training_cfg.get("max_steps") is not None:
        update_groups = update_groups[: int(training_cfg["max_steps"])]
    total_steps = len(update_groups)
    if total_steps <= 0:
        raise TrainingContractError("training plan contains no optimizer steps")
    trainable_parameters = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer = torch.optim.AdamW(trainable_parameters, lr=float(training_cfg["learning_rate"]))
    warmup_steps = int(total_steps * float(training_cfg["warmup_ratio"]))
    scheduler = get_cosine_schedule_with_warmup(optimizer, warmup_steps, total_steps)
    resume_step = 0
    if resume_manifest is not None:
        resume_step = _restore_trainer_state(resume_manifest.path / "trainer_state.pt", optimizer, scheduler, torch)
        if resume_step != resume_manifest.step or resume_step > total_steps:
            raise TrainingContractError("resume checkpoint step is inconsistent with the current training plan")

    output_root = Path(str(runtime["output_root"])).expanduser().resolve()
    run_id = str(runtime.get("run_id", ""))
    if not run_id:
        raise TrainingContractError("Ray Driver must allocate the MLflow Run ID before training")
    attempt_id = str(runtime.get("attempt_id", f"attempt-{run_id}"))
    tracking_context = runtime["tracking_context"]
    tracking_uri = runtime.get("tracking_uri") or os.environ.get("MLFLOW_TRACKING_URI")
    metadata_path = Path(str(runtime["job_metadata_path"])).expanduser().resolve()
    save_steps = int(training_cfg["save_steps"])
    eval_steps = int(training_cfg["eval_steps"])
    checkpoint: CheckpointManifest | None = resume_manifest
    losses_weighted = 0.0
    train_target_tokens = 0
    train_samples_seen = 0
    validation_loss = math.nan
    validation_tokens = 0
    validation_seconds = 0.0
    validation_tokens_processed = 0
    validation_evaluations = 0
    checkpoint_seconds = 0.0
    checkpoint_upload_seconds = 0.0
    checkpoint_count = 0
    optimization_compute_seconds = 0.0
    started = time.perf_counter()
    torch.cuda.reset_peak_memory_stats(device)
    optimizer.zero_grad(set_to_none=True)
    step = resume_step
    try:
        for group_index in range(resume_step, total_steps):
            group = update_groups[group_index]
            optimization_started = time.perf_counter()
            step_weighted_loss = 0.0
            step_target_tokens = 0
            step_samples = 0
            for indices in group:
                batch = [encoded_train[index] for index in indices]
                input_ids, labels, attention_mask, batch_targets = _batch_tensors(batch, tokenizer, device)
                output = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
                loss = output.loss
                (loss / len(group)).backward()
                step_weighted_loss += float(loss.detach().cpu()) * batch_targets
                step_target_tokens += batch_targets
                step_samples += len(batch)
            grad_norm = float(
                torch.nn.utils.clip_grad_norm_(trainable_parameters, float(training_cfg["max_grad_norm"])).detach().cpu()
            )
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad(set_to_none=True)
            optimization_compute_seconds += time.perf_counter() - optimization_started
            step = group_index + 1
            losses_weighted += step_weighted_loss
            train_target_tokens += step_target_tokens
            train_samples_seen += step_samples
            step_loss = step_weighted_loss / max(step_target_tokens, 1)
            step_metrics = {
                "train_loss": step_loss,
                "learning_rate": float(scheduler.get_last_lr()[0]),
                "gradient_norm": grad_norm,
            }
            if step % eval_steps == 0 or step == total_steps:
                validation_loss, validation_tokens, elapsed = _evaluate_validation_loss(
                    model, encoded_validation, tokenizer, device, eval_batch_size
                )
                validation_seconds += elapsed
                validation_tokens_processed += validation_tokens
                validation_evaluations += 1
                step_metrics.update(
                    {
                        "validation_loss": validation_loss,
                        "validation_perplexity": math.exp(min(validation_loss, 20.0)),
                    }
                )
            log_training_metrics(tracking_context, step_metrics, tracking_uri, step=step)
            if step % save_steps == 0 or step == total_steps:
                checkpoint_started = time.perf_counter()
                checkpoint = _save_training_checkpoint(
                    model,
                    tokenizer,
                    optimizer,
                    scheduler,
                    torch,
                    output_root,
                    run_id,
                    attempt_id,
                    step,
                    {
                        "config_digest": config_digest,
                        "dataset_manifest_digest": dataset_digest,
                        "split_digest": splits.digest,
                        "split_counts": splits.counts,
                        "seed": seed,
                        "model_revision": str(model_cfg.get("revision") or model_cfg["id"]),
                        "environment_digest": environment_digest(),
                    },
                )
                checkpoint_seconds += time.perf_counter() - checkpoint_started
                checkpoint_count += 1
                current_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                write_job_metadata_atomic(
                    update_checkpoint_pointer(current_metadata, checkpoint_record(checkpoint)), metadata_path
                )
                checkpoint_upload_started = time.perf_counter()
                log_artifact_directory(
                    tracking_context,
                    checkpoint.path,
                    f"checkpoints/{attempt_id}/step-{step}",
                    tracking_uri,
                )
                checkpoint_upload_seconds += time.perf_counter() - checkpoint_upload_started
    except KeyboardInterrupt:
        return TrainResult(
            run_id,
            attempt_id,
            "interrupted",
            step,
            {"train_loss": losses_weighted / max(train_target_tokens, 1)},
            checkpoint,
            {"status": "interrupted"},
        )

    wall_seconds = time.perf_counter() - started
    if math.isnan(validation_loss):
        validation_loss, validation_tokens, elapsed = _evaluate_validation_loss(
            model, encoded_validation, tokenizer, device, eval_batch_size
        )
        validation_seconds += elapsed
        validation_tokens_processed += validation_tokens
        validation_evaluations += 1
    training_peak_gpu_memory_mib = float(torch.cuda.max_memory_allocated(device) / 2**20)
    training_peak_gpu_reserved_mib = float(torch.cuda.max_memory_reserved(device) / 2**20)
    base_validation_loss = math.nan
    base_validation_tokens = 0
    base_validation_seconds = 0.0
    if not bool(config.values["evaluation"].get("compare_base", True)):
        base_quality: dict[str, float | str] = {}
    else:
        with model.disable_adapter():
            base_validation_loss, base_validation_tokens, elapsed = _evaluate_validation_loss(
                model, encoded_validation, tokenizer, device, eval_batch_size
            )
            base_validation_seconds += elapsed
        base_quality = evaluate_generation_quality(
            model,
            tokenizer,
            validation_samples,
            device=device,
            max_input_tokens=max_length,
            max_new_tokens=int(config.values["generation"]["max_new_tokens"]),
            batch_size=int(config.values["evaluation"].get("generation_batch_size", 8)),
            adapter_enabled=False,
        )
    lora_quality = evaluate_generation_quality(
        model,
        tokenizer,
        validation_samples,
        device=device,
        max_input_tokens=max_length,
        max_new_tokens=int(config.values["generation"]["max_new_tokens"]),
        batch_size=int(config.values["evaluation"].get("generation_batch_size", 8)),
        adapter_enabled=True,
    )
    test_evaluation_id: str | None = None
    final_test_report: dict[str, Any] | None = None
    test_metrics: dict[str, float] = {}
    if bool(config.values["evaluation"].get("evaluate_test")):
        if str(config.values["run"]["role"]) != "champion":
            raise TrainingContractError("only a champion Run may evaluate the final test split")
        candidate_evidence_digest = str(runtime.get("candidate_evidence_digest") or "")
        if not candidate_evidence_digest.startswith("sha256:"):
            raise TrainingContractError("champion test evaluation requires a Galatea candidate evidence digest")
        try:
            claim = claim_test_evaluation(
                candidate_evidence_digest,
                splits.digest,
                output_root / "test-evaluation-ledger.json",
            )
        except EvaluationProtocolError as exc:
            raise TrainingContractError(str(exc)) from exc
        test_evaluation_id = claim.test_evaluation_id
        test_samples = splits.samples_by_split["test"]
        base_test_loss, base_test_tokens, base_test_seconds = (math.nan, 0, 0.0)
        with model.disable_adapter():
            base_test_loss, base_test_tokens, base_test_seconds = _evaluate_validation_loss(
                model, _encode_samples(tokenizer, test_samples, max_length, enable_thinking), tokenizer, device, eval_batch_size
            )
        base_test_quality = evaluate_generation_quality(
            model,
            tokenizer,
            test_samples,
            device=device,
            max_input_tokens=max_length,
            max_new_tokens=int(config.values["generation"]["max_new_tokens"]),
            batch_size=int(config.values["evaluation"].get("generation_batch_size", 8)),
            adapter_enabled=False,
        )
        lora_test_loss, lora_test_tokens, lora_test_seconds = _evaluate_validation_loss(
            model, _encode_samples(tokenizer, test_samples, max_length, enable_thinking), tokenizer, device, eval_batch_size
        )
        lora_test_quality = evaluate_generation_quality(
            model,
            tokenizer,
            test_samples,
            device=device,
            max_input_tokens=max_length,
            max_new_tokens=int(config.values["generation"]["max_new_tokens"]),
            batch_size=int(config.values["evaluation"].get("generation_batch_size", 8)),
            adapter_enabled=True,
        )
        test_metrics = {
            "base_test_loss": float(base_test_loss),
            "base_test_perplexity": math.exp(min(base_test_loss, 20.0)),
            "lora_test_loss": float(lora_test_loss),
            "lora_test_perplexity": math.exp(min(lora_test_loss, 20.0)),
            "test_loss_improvement": float(base_test_loss - lora_test_loss),
            "base_test_target_tokens": float(base_test_tokens),
            "lora_test_target_tokens": float(lora_test_tokens),
            "base_test_target_tokens_per_second": base_test_tokens / max(base_test_seconds, 1e-9),
            "lora_test_target_tokens_per_second": lora_test_tokens / max(lora_test_seconds, 1e-9),
        }
        for prefix, quality_metrics in (("base_test_generation", base_test_quality), ("lora_test_generation", lora_test_quality)):
            for key, value in quality_metrics.items():
                if isinstance(value, (int, float)):
                    test_metrics[f"{prefix}_{key}"] = float(value)
        final_test_report = {
            "schema_version": "final-test-evaluation-v1",
            "split": "test",
            "split_digest": splits.digest,
            "test_evaluation_id": test_evaluation_id,
            "freeze_id": candidate_evidence_digest,
            "sample_count": len(test_samples),
            "output_text_persisted": False,
            "base": {"validation_loss": base_test_loss, "validation_perplexity": math.exp(min(base_test_loss, 20.0)), **base_test_quality},
            "lora": {"validation_loss": lora_test_loss, "validation_perplexity": math.exp(min(lora_test_loss, 20.0)), **lora_test_quality},
        }
    try:
        import resource

        process_max_rss_mib = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    except (ImportError, AttributeError):
        process_max_rss_mib = math.nan
    metrics = {
        "train_loss": losses_weighted / max(train_target_tokens, 1),
        "validation_loss": validation_loss,
        "validation_perplexity": math.exp(min(validation_loss, 20.0)),
        "base_validation_loss": base_validation_loss,
        "base_validation_perplexity": math.exp(min(base_validation_loss, 20.0)),
        "validation_loss_improvement": base_validation_loss - validation_loss,
        "optimizer_steps": float(step),
        "train_sample_count": float(len(train_samples)),
        "validation_sample_count": float(len(validation_samples)),
        "test_sample_count_untouched": float(len(splits.samples_by_split["test"])),
        "train_samples_seen": float(train_samples_seen),
        "train_target_tokens": float(train_target_tokens),
        "validation_target_tokens": float(validation_tokens),
        "validation_target_tokens_processed": float(validation_tokens_processed),
        "validation_evaluations": float(validation_evaluations),
        "base_validation_target_tokens": float(base_validation_tokens),
        "data_preparation_wall_seconds": data_preparation_seconds,
        "model_load_wall_seconds": model_load_seconds,
        "tokenization_wall_seconds": tokenization_seconds,
        "training_wall_seconds": wall_seconds,
        "optimization_compute_wall_seconds": optimization_compute_seconds,
        "validation_wall_seconds": validation_seconds,
        "base_validation_wall_seconds": base_validation_seconds,
        "checkpoint_wall_seconds": checkpoint_seconds,
        "checkpoint_upload_wall_seconds": checkpoint_upload_seconds,
        "checkpoint_count": float(checkpoint_count),
        "train_samples_per_second": train_samples_seen / wall_seconds,
        "train_target_tokens_per_second": train_target_tokens / wall_seconds,
        "optimizer_steps_per_second": (step - resume_step) / wall_seconds,
        "optimizer_compute_samples_per_second": train_samples_seen / max(optimization_compute_seconds, 1e-9),
        "optimizer_compute_target_tokens_per_second": train_target_tokens / max(optimization_compute_seconds, 1e-9),
        "optimizer_compute_steps_per_second": (step - resume_step) / max(optimization_compute_seconds, 1e-9),
        "validation_target_tokens_per_second": validation_tokens_processed / max(validation_seconds, 1e-9),
        "base_validation_target_tokens_per_second": base_validation_tokens / max(base_validation_seconds, 1e-9),
        "training_peak_gpu_memory_mib": training_peak_gpu_memory_mib,
        "training_peak_gpu_reserved_mib": training_peak_gpu_reserved_mib,
        "process_max_rss_mib": process_max_rss_mib,
    }
    metrics.update(test_metrics)
    for prefix, quality_metrics in (("base_generation", base_quality), ("lora_generation", lora_quality)):
        for key, value in quality_metrics.items():
            if isinstance(value, (int, float)):
                metrics[f"{prefix}_{key}"] = float(value)
    if base_quality:
        metrics["auxiliary_quality_score_improvement"] = float(lora_quality["auxiliary_quality_score"]) - float(base_quality["auxiliary_quality_score"])
    metrics["worker_wall_seconds"] = time.perf_counter() - worker_started
    validate_required_evidence_metrics(metrics)
    quality_report = {
        "schema_version": "validation-generation-quality-v2",
        "split": "validation",
        "split_digest": splits.digest,
        "sample_count": len(validation_samples),
        "test_access": "untouched",
        "output_text_persisted": False,
        "primary_quality_metric": "validation_loss",
        "quality_score_definition": "100*(0.45*token_f1+0.35*rouge_l+0.10*exact_match+0.10*non_empty)*(1-0.25*repetition_3gram_rate)*(1-0.10*max_length_stop_rate)",
        "base": ({"validation_loss": base_validation_loss, "validation_perplexity": math.exp(min(base_validation_loss, 20.0)), **base_quality} if base_quality else None),
        "lora": {"validation_loss": validation_loss, "validation_perplexity": math.exp(min(validation_loss, 20.0)), **lora_quality},
    }
    quality_report_path = output_root / run_id / attempt_id / "validation_quality.json"
    quality_report_path.parent.mkdir(parents=True, exist_ok=True)
    quality_report_path.write_text(json.dumps(quality_report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    log_artifact_with_sha256(tracking_context, quality_report_path, "reports", tracking_uri)
    final_test_path: Path | None = None
    if final_test_report is not None:
        final_test_path = output_root / run_id / attempt_id / "final-test-evaluation.json"
        final_test_path.write_text(json.dumps(final_test_report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        log_artifact_with_sha256(tracking_context, final_test_path, "reports", tracking_uri)
    manifest = {
        "schema_version": "llm-lora-training-run-v2",
        "project": "llm-lora-playground",
        "run_kind": str(config.values.get("run_kind", "training")),
        "role": str(config.values["run"]["role"]),
        "run_id": run_id,
        "mlflow_run_id": run_id,
        "attempt_id": attempt_id,
        "status": "completed",
        "config_digest": config_digest,
        "dataset_manifest_digest": dataset_digest,
        "dataset": {
            "dataset_id": str(config.values["data"]["dataset_id"]),
            "content_sha256": dataset_digest,
            "preprocessing_version": str(config.values["data"]["preprocessing_version"]),
            "sample_count": len(samples),
        },
        "split_digest": splits.digest,
        "split_counts": splits.counts,
        "split": {
            "strategy": splits.strategy,
            "manifest_sha256": splits.digest,
            "counts": splits.counts,
        },
        "test_access": "enabled_once" if test_evaluation_id else "untouched",
        "test_evaluation_id": test_evaluation_id,
        "model": {
            "id": str(model_cfg["id"]),
            "revision": str(model_cfg.get("revision") or model_cfg["id"]),
            "dtype": str(model_cfg["dtype"]),
            "device": device,
            "max_sequence_length": max_length,
        },
        "code_revision": str(runtime.get("code_revision") or os.environ.get("CODE_REVISION", "unresolved")),
        "environment_digest": str(runtime.get("environment_digest") or environment_digest()),
        "seed": seed,
        "resources": dict(config.values["resources"]),
        "objective_metric": str(config.values["objective_metric"]),
        "objective_mode": str(config.values["objective_mode"]),
        "governance": {
            "promotable": bool(runtime.get("promotable", False)),
            "formal_training_eligible": bool(config.values.get("experiment", {}).get("formal_training_eligible", True)),
            "human_review_completed": bool(config.values.get("experiment", {}).get("human_review_completed", False)),
            "quality_evidence_status": config.values.get("experiment", {}).get("quality_evidence_status"),
        },
        "metrics": metrics,
        "checkpoint": checkpoint_record(checkpoint) if checkpoint else None,
        "artifacts": [
            "manifests/run_manifest.json",
            "reports/validation_quality.json",
            "model/adapter_config.json",
            "model/adapter_model.safetensors",
        ] + (["reports/final-test-evaluation.json"] if final_test_report is not None else []),
        "execution": {
            "mode": str(runtime["execution_mode"]),
            "ray_job_id": str(runtime["ray_job_id"]),
            "ray_submission_id": runtime.get("ray_submission_id"),
            "release_id": runtime.get("release_id"),
            "readiness_digest": runtime.get("readiness_digest"),
            "execution_identity": runtime.get("execution_identity"),
        },
    }
    manifest_path = output_root / run_id / attempt_id / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    log_artifact_with_sha256(tracking_context, manifest_path, "manifests", tracking_uri)
    if checkpoint is None:
        raise TrainingContractError("completed training has no checkpoint")
    log_artifact_directory(tracking_context, checkpoint.path / "adapter", "model", tracking_uri)
    set_training_tags(
        tracking_context,
        {
            "model.uri": f"runs:/{run_id}/model",
            "run.outcome": "succeeded",
            "test.access": "enabled_once" if test_evaluation_id else "untouched",
            **({"test.evaluated": True, "test.evaluation_id": test_evaluation_id} if test_evaluation_id else {}),
        },
        tracking_uri,
    )
    log_training_metrics(tracking_context, metrics, tracking_uri, step=step)
    return TrainResult(run_id, attempt_id, "completed", step, metrics, checkpoint, manifest)
