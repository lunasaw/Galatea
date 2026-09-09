"""Governed LoRA training and evaluation owned by the fixed Ray Driver."""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from .admission import require as require_admission
from .artifacts import verify_artifact_roundtrip
from .consent import ConsentError, verify_consent
from .runtime import validate_project_config


class TrainingBoundaryError(RuntimeError):
    pass


def _model_source(model_config: Mapping[str, Any]) -> tuple[str, str | None]:
    """Resolve the approved model identity to a node-local immutable snapshot.

    The model ID and revisions remain part of the signed binding and MLflow
    lineage.  The service may provide a read-only local snapshot through the
    protected runtime environment when the Ray node has no Hugging Face
    network access; callers cannot choose this path.
    """
    model_id = str(model_config["model_id"])
    local_path = os.environ.get("WECHAT_PERSONA_MODEL_PATH")
    if local_path and model_id == "Qwen/Qwen3.5-0.8B":
        path = Path(local_path)
        if path.is_dir() and (path / "config.json").is_file():
            return str(path), None
    return model_id, str(model_config.get("model_revision"))


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _is_revision(value: Any) -> bool:
    text = str(value or "")
    return len(text) in {40, 64} and all(character in "0123456789abcdef" for character in text)


def build_training_plan(config: Mapping[str, Any]) -> dict[str, Any]:
    errors = validate_training_readiness(config)
    return {
        "status": "blocked" if errors else "planned",
        "project": config.get("project"),
        "task": config.get("task"),
        "config_digest": _digest(config),
        "role": config.get("run", {}).get("role"),
        "test_access": config.get("evaluation", {}).get("test_access", "untouched"),
        "execution_backend": config.get("execution", {}).get("backend"),
        "errors": errors,
        "will_create_mlflow_run": False,
    }


def validate_training_readiness(config: Mapping[str, Any]) -> list[str]:
    """Check immutable data, review, model and protocol gates without fitting."""

    errors = list(validate_project_config(config))
    dataset = config.get("dataset", {})
    governance = config.get("governance", config.get("experiment", {}))
    counts = dataset.get("counts", {})
    for split in ("train", "validation", "test"):
        if counts and int(counts.get(split, 0)) <= 0:
            errors.append(f"dataset.{split} must be non-empty")
    if dataset.get("formal_dataset_ready") is not True:
        errors.append("dataset must be FORMAL_DATASET_READY")
    if governance:
        required_true = (
            "formal_training_eligible",
            "human_review_completed",
            "pii_scan_passed",
            "canary_scan_passed",
        )
        for name in required_true:
            if governance.get(name) is not True:
                errors.append(f"{name} must be true")
        if governance.get("withdrawn") is True:
            errors.append("withdrawn dataset cannot train")
        if governance.get("cross_split_session_count", 0) != 0:
            errors.append("cross_split_session_count must be zero")
        evidence = governance.get("canary_scan_evidence", {})
        if not isinstance(evidence, Mapping):
            errors.append("canary_scan_evidence is required")
        else:
            if not _is_sha(evidence.get("report_sha256")):
                errors.append("canary_scan_evidence.report_sha256 must be a SHA-256 digest")
            if int(evidence.get("match_count", -1)) != 0:
                errors.append("canary scan match_count must be zero")
            if int(evidence.get("scanned_sample_count", 0)) <= 0:
                errors.append("canary scan must cover the formal snapshot")
            if not evidence.get("scanner_version"):
                errors.append("canary scan scanner_version is required")
    training = config.get("training", {})
    if config.get("run", {}).get("role") in {"trial", "baseline"} and training.get("epochs") != 1:
        errors.append("initial Trial/baseline epochs must be 1")
    for key in ("model_revision", "tokenizer_revision"):
        if not _is_revision(config.get("model", {}).get(key)):
            errors.append(f"model.{key} must be an immutable commit revision")
    consent_ledger = dataset.get("consent_ledger")
    if consent_ledger:
        try:
            verify_consent(
                Path(str(consent_ledger)),
                required_purposes={"persona_style", "evaluation"},
            )
        except (ConsentError, OSError) as exc:
            errors.append(str(exc))
    return sorted(set(errors))


def load_bound_config(path: Path, binding: Mapping[str, Any]) -> dict[str, Any]:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding.get("config_digest"):
        raise TrainingBoundaryError("embedded config digest differs from MCP execution binding")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TrainingBoundaryError("bound config must contain a JSON object")
    if value.get("seed") != value.get("training", {}).get("seed"):
        raise TrainingBoundaryError("MCP seed differs from workload training.seed")
    return value


def _download_jsonl_view(ref: Mapping[str, Any], s3_client: Any) -> list[dict[str, Any]]:
    if int(ref["size_bytes"]) > 1024 * 1024 * 1024:
        raise ValueError("dataset view exceeds workload size limit")
    response = s3_client.get_object(
        Bucket=ref["bucket"], Key=ref["key"], VersionId=ref["version_id"]
    )
    body = response["Body"]
    try:
        if response.get("VersionId") != ref["version_id"]:
            raise ValueError("dataset object version mismatch")
        if int(response.get("ContentLength", -1)) != int(ref["size_bytes"]):
            raise ValueError("dataset object size mismatch")
        raw = body.read(int(ref["size_bytes"]) + 1)
    finally:
        body.close()
    if len(raw) != int(ref["size_bytes"]) or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise ValueError("dataset object digest mismatch")
    rows: list[dict[str, Any]] = []
    for line in raw.decode("utf-8").splitlines():
        if line.strip():
            row = json.loads(
                line,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")),
            )
            if not isinstance(row, dict):
                raise ValueError("dataset row must be an object")
            rows.append(row)
    if not rows:
        raise ValueError("dataset view must be non-empty")
    return rows


def _messages(row: Mapping[str, Any]) -> list[dict[str, str]]:
    values = row.get("messages")
    if not isinstance(values, list) or not values:
        raise ValueError("training row messages are required")
    result: list[dict[str, str]] = []
    for message in values:
        if not isinstance(message, Mapping) or message.get("role") not in {
            "system",
            "user",
            "assistant",
        }:
            raise ValueError("invalid chat message")
        result.append({"role": str(message["role"]), "content": str(message.get("content", ""))})
    if result[-1]["role"] != "assistant" or not result[-1]["content"].strip():
        raise ValueError("SFT row must end with a non-empty assistant response")
    return result


def _load_model(config: Mapping[str, Any]):
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model_config = config["model"]
    model_source, revision = _model_source(model_config)
    tokenizer_source, tokenizer_revision = _model_source(model_config)
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_source,
        revision=tokenizer_revision,
        trust_remote_code=False,
    )
    model = AutoModelForCausalLM.from_pretrained(
        model_source,
        revision=revision,
        torch_dtype=getattr(torch, str(model_config.get("dtype", "bfloat16"))),
        trust_remote_code=False,
    )
    lora = config["lora"]
    return tokenizer, get_peft_model(
        model,
        LoraConfig(
            r=int(lora["rank"]),
            lora_alpha=int(lora["alpha"]),
            lora_dropout=float(lora["dropout"]),
            target_modules=list(lora["target_modules"]),
            task_type="CAUSAL_LM",
        ),
    )


def _tokenize_rows(tokenizer: Any, rows: Iterable[Mapping[str, Any]], max_length: int):
    from datasets import Dataset

    encoded: list[dict[str, list[int]]] = []
    for row in rows:
        messages = _messages(row)
        full_text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=False
        )
        prompt_text = tokenizer.apply_chat_template(
            messages[:-1], tokenize=False, add_generation_prompt=True
        )
        # Tokenize without an implicit right-truncation first.  Right
        # truncation can remove the entire assistant target when a long chat
        # prompt fills the context window, yielding an unusable all-ignored
        # label row.  Keep the deterministic suffix of the complete example,
        # which always contains the assistant response and therefore preserves
        # the supervised signal.
        full = tokenizer(full_text, truncation=False)
        prompt = tokenizer(prompt_text, truncation=False)
        labels = list(full["input_ids"])
        prompt_length = min(len(prompt["input_ids"]), len(labels))
        labels[:prompt_length] = [-100] * prompt_length
        if len(labels) > max_length:
            start = len(labels) - max_length
            full["input_ids"] = list(full["input_ids"])[start:]
            full["attention_mask"] = list(full["attention_mask"])[start:]
            labels = labels[start:]
        if not any(value != -100 for value in labels):
            raise ValueError("assistant response was fully truncated")
        encoded.append(
            {
                "input_ids": list(full["input_ids"]),
                "attention_mask": list(full["attention_mask"]),
                "labels": labels,
            }
        )
    return Dataset.from_list(encoded)


def _example_losses(model: Any, dataset: Any, *, batch_size: int = 4) -> list[float]:
    """Compute one causal-LM loss per example using padded inference batches.

    The previous implementation launched one GPU forward pass per validation
    row.  That made the post-training validation tail take several minutes and
    could consume the governed execution deadline even after Trainer had
    finished.  Batched logits are reduced per row so the metric remains the
    same as the single-example cross-entropy while using the declared eval
    batch size.
    """
    import torch
    import torch.nn.functional as F

    model.eval()
    batch_size = max(1, int(batch_size))
    pad_token_id = getattr(model.config, "pad_token_id", None)
    if pad_token_id is None:
        pad_token_id = getattr(model.config, "eos_token_id", 0)
    losses: list[float] = []
    rows = list(dataset)
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        input_ids = torch.nn.utils.rnn.pad_sequence(
            [torch.tensor(row["input_ids"], dtype=torch.long) for row in batch],
            batch_first=True,
            padding_value=int(pad_token_id),
        ).to(model.device)
        attention_mask = torch.nn.utils.rnn.pad_sequence(
            [torch.tensor(row["attention_mask"], dtype=torch.long) for row in batch],
            batch_first=True,
            padding_value=0,
        ).to(model.device)
        labels = torch.nn.utils.rnn.pad_sequence(
            [torch.tensor(row["labels"], dtype=torch.long) for row in batch],
            batch_first=True,
            padding_value=-100,
        ).to(model.device)
        with torch.no_grad():
            logits = model(input_ids=input_ids, attention_mask=attention_mask).logits
        shifted_logits = logits[:, :-1, :].contiguous()
        shifted_labels = labels[:, 1:].contiguous()
        token_losses = F.cross_entropy(
            shifted_logits.float().view(-1, shifted_logits.shape[-1]),
            shifted_labels.view(-1),
            reduction="none",
            ignore_index=-100,
        ).view(shifted_labels.shape)
        valid = shifted_labels.ne(-100)
        counts = valid.sum(dim=1)
        if torch.any(counts == 0):
            raise ValueError("example has no supervised assistant tokens")
        per_example = token_losses.sum(dim=1) / counts
        losses.extend(float(value) for value in per_example.detach().cpu())
    return losses


def _generated_safety_metrics(
    model: Any,
    tokenizer: Any,
    rows: list[dict[str, Any]],
    *,
    maximum_cases: int,
    maximum_new_tokens: int,
) -> dict[str, float]:
    import torch

    from .redact import scan_redacted_text
    from .safety import evaluate_safety

    outputs: list[str] = []
    for row in sorted(rows, key=lambda value: str(value.get("sample_id", "")))[:maximum_cases]:
        prompt = _messages(row)[:-1]
        text = tokenizer.apply_chat_template(prompt, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt", truncation=True).to(model.device)
        with torch.no_grad():
            generated = model.generate(
                **inputs,
                do_sample=False,
                max_new_tokens=maximum_new_tokens,
                pad_token_id=tokenizer.eos_token_id,
            )
        completion = generated[0, inputs["input_ids"].shape[1] :]
        outputs.append(tokenizer.decode(completion, skip_special_tokens=True))
    safety = evaluate_safety(outputs)
    pii_leaks = sum(scan_redacted_text(value)["hard_leak_count"] > 0 for value in outputs)
    canary_leaks = sum("canary" in value.casefold() for value in outputs)
    unsafe = sum(
        int(safety[key])
        for key in (
            "impersonation_count",
            "dependency_manipulation_count",
            "guilt_manipulation_count",
        )
    )
    return {
        "test_pii_leak_count": float(pii_leaks),
        "test_canary_leak_count": float(canary_leaks),
        "test_unsafe_behavior_count": float(unsafe),
        "test_safety_case_count": float(len(outputs)),
    }


def _create_run(client: Any, binding: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    tags = {
        "galatea.project": str(binding["project_id"]),
        "galatea.campaign": str(binding["campaign_id"]),
        "galatea.operation": str(binding["operation_id"]),
        "galatea.submission": str(binding["submission_id"]),
        "galatea.role": str(binding["role"]),
        "run.role": str(binding["role"]),
        "run.promotable": str(bool(config.get("run", {}).get("promotable", False))).lower(),
        "run.outcome": "running",
        "artifact.roundtrip_verified": "false",
        "test.access": "authorized" if binding["role"] == "evaluate" else "untouched",
    }
    return str(client.create_run(str(binding["experiment_id"]), tags=tags).info.run_id)


def _log_lineage(client: Any, run_id: str, binding: Mapping[str, Any], config: Mapping[str, Any]) -> None:
    params = {
        "task": config["task"],
        "config_digest": binding["config_digest"],
        "release_digest": binding["release_digest"],
        "dataset_digest": binding["dataset_digest"],
        "split_digest": binding["split_digest"],
        "preprocessing": binding["preprocessing"],
        "metric_definition": binding["metric_definition"],
        "evaluation_protocol": binding["evaluation_protocol"],
        "model_id": config["model"]["model_id"],
        "model_revision": config["model"]["model_revision"],
        "tokenizer_revision": config["model"]["tokenizer_revision"],
        "seed": binding["seed"],
        "attempt": binding["attempt"],
        "readiness_digest": binding["readiness_digest"],
        "code_revision": binding["code_revision"],
        "environment_digest": binding["environment_digest"],
    }
    for key, value in params.items():
        client.log_param(run_id, key, str(value))


def _download_proxy_model(client: Any, run_id: str, destination: Path) -> Path:
    run = client.get_run(run_id)
    if not str(run.info.artifact_uri).startswith("mlflow-artifacts:"):
        raise ValueError("champion model must use the MLflow artifact proxy")
    path = Path(client.download_artifacts(run_id, "model", str(destination))).resolve()
    if not path.is_dir() or path.is_symlink():
        raise ValueError("downloaded champion artifact is invalid")
    return path


def _verify_fresh_adapter_load(model_directory: Path) -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; from peft import PeftConfig; "
                "from safetensors import safe_open; import sys; "
                "root=Path(sys.argv[1]); PeftConfig.from_pretrained(root); "
                "handle=safe_open(root/'adapter_model.safetensors',framework='pt',device='cpu'); "
                "assert handle.keys()"
            ),
            str(model_directory),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("fresh-process adapter load verification failed")


def _train_role(
    config: Mapping[str, Any],
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    client: Any,
    run_id: str,
    output: Path,
) -> tuple[dict[str, float], list[tuple[Path, str]]]:
    from transformers import DataCollatorForSeq2Seq, Trainer, TrainingArguments, set_seed

    set_seed(int(config["training"]["seed"]))
    tokenizer, model = _load_model(config)
    maximum = int(config["training"].get("max_length", 1024))
    train_data = _tokenize_rows(tokenizer, train_rows, maximum)
    validation_data = _tokenize_rows(tokenizer, validation_rows, maximum)
    adapter_dir = output / "model"
    trainer = Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=str(output / "checkpoints"),
            num_train_epochs=float(config["training"]["epochs"]),
            max_steps=int(config["training"].get("max_steps", -1) or -1),
            per_device_train_batch_size=int(config["training"]["batch_size"]),
            per_device_eval_batch_size=int(config["training"].get("eval_batch_size", 1)),
            learning_rate=float(config["training"]["learning_rate"]),
            seed=int(config["training"]["seed"]),
            logging_steps=1,
            eval_strategy="epoch",
            save_strategy="epoch",
            save_total_limit=1,
            load_best_model_at_end=True,
            report_to=[],
        ),
        train_dataset=train_data,
        eval_dataset=validation_data,
        data_collator=DataCollatorForSeq2Seq(tokenizer, padding=True),
    )
    train = trainer.train()
    validation = trainer.evaluate()
    model.save_pretrained(adapter_dir, safe_serialization=True)
    tokenizer.save_pretrained(adapter_dir)
    validation_losses = _example_losses(
        model,
        validation_data,
        batch_size=int(config["training"].get("eval_batch_size", 4)),
    )
    adapted_validation_loss = sum(validation_losses) / len(validation_losses)
    metrics = {
        "train_loss": float(train.training_loss),
        "val_loss": adapted_validation_loss,
        "val_perplexity": math.exp(min(adapted_validation_loss, 20.0)),
    }
    for entry in trainer.state.log_history:
        step = int(entry.get("step", 0))
        for source, target in (
            ("loss", "train_loss"),
            ("learning_rate", "learning_rate"),
            ("grad_norm", "gradient_norm"),
        ):
            if source in entry:
                client.log_metric(run_id, target, float(entry[source]), step=step)
    report = output / "validation-quality.json"
    report.write_text(json.dumps({"metrics": metrics}, sort_keys=True) + "\n", encoding="utf-8")
    if not trainer.state.best_model_checkpoint:
        raise ValueError("best checkpoint was not produced")
    best_checkpoint = Path(trainer.state.best_model_checkpoint)
    return metrics, [
        (adapter_dir / "adapter_model.safetensors", "model/adapter_model.safetensors"),
        (adapter_dir / "adapter_config.json", "model/adapter_config.json"),
        (report, "reports/validation-quality.json"),
        (best_checkpoint / "adapter_model.safetensors", "checkpoints/best-adapter.safetensors"),
        (best_checkpoint / "trainer_state.json", "checkpoints/trainer_state.json"),
    ]


def _evaluate_role(
    binding: Mapping[str, Any],
    config: Mapping[str, Any],
    test_rows: list[dict[str, Any]],
    client: Any,
    output: Path,
) -> tuple[dict[str, float], list[tuple[Path, str]]]:
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model_config = config["model"]
    model_source, revision = _model_source(model_config)
    tokenizer_source, tokenizer_revision = _model_source(model_config)
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_source, revision=tokenizer_revision, trust_remote_code=False
    )
    base = AutoModelForCausalLM.from_pretrained(
        model_source, revision=revision, trust_remote_code=False
    )
    adapter_directory = _download_proxy_model(
        client,
        str(binding["champion_run_id"]),
        output / "champion-download",
    )
    adapter_file = adapter_directory / "adapter_model.safetensors"
    if _file_digest(adapter_file) != binding["champion_model_sha256"]:
        raise ValueError("champion model digest mismatch")
    _verify_fresh_adapter_load(adapter_directory)
    model = PeftModel.from_pretrained(base, str(adapter_directory), is_trainable=False)
    model.eval()
    dataset = _tokenize_rows(tokenizer, test_rows, int(config["training"].get("max_length", 1024)))
    test_losses = _example_losses(model, dataset)
    test_loss = sum(test_losses) / len(test_losses)
    safety = _generated_safety_metrics(
        model,
        tokenizer,
        test_rows,
        maximum_cases=int(config["evaluation"].get("safety_max_cases", 256)),
        maximum_new_tokens=int(config["evaluation"].get("safety_max_new_tokens", 64)),
    )
    metrics = {
        "test_loss": test_loss,
        "test_perplexity": math.exp(min(test_loss, 20.0)),
        **safety,
    }
    report = output / "final-test-evaluation.json"
    report.write_text(
        json.dumps({"metrics": metrics, "sample_count": len(test_rows)}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metrics, [
        (adapter_file, "model/adapter_model.safetensors"),
        (adapter_directory / "adapter_config.json", "model/adapter_config.json"),
        (report, "reports/final-test-evaluation.json"),
    ]


def run_training(
    config: Mapping[str, Any],
    *,
    binding: Mapping[str, Any],
    admission: Any,
    s3_client: Any,
    mlflow_client: Any,
) -> dict[str, Any]:
    """Execute one admitted role; direct calls cannot obtain ``admission``."""

    require_admission(admission, binding)
    errors = validate_training_readiness(config)
    if errors:
        raise TrainingBoundaryError("training readiness failed: " + "; ".join(errors))
    role = str(binding["role"])
    if role != config.get("run", {}).get("role"):
        raise TrainingBoundaryError("MCP execution role differs from bound configuration")
    run_id = _create_run(mlflow_client, binding, config)
    started = time.monotonic()
    try:
        _log_lineage(mlflow_client, run_id, binding, config)
        views = {
            name: _download_jsonl_view(reference, s3_client)
            for name, reference in binding["views"].items()
        }
        with tempfile.TemporaryDirectory(prefix=f"wechat-persona-{binding['operation_id']}-") as temp:
            output = Path(temp)
            if role == "evaluate":
                metrics, artifacts = _evaluate_role(binding, config, views["test"], mlflow_client, output)
            else:
                metrics, artifacts = _train_role(
                    config, views["train"], views["validation"], mlflow_client, run_id, output
                )
            for name, value in metrics.items():
                mlflow_client.log_metric(run_id, name, float(value))
            manifest: list[dict[str, Any]] = []
            for source, remote in artifacts:
                parent = str(Path(remote).parent)
                mlflow_client.log_artifact(run_id, str(source), parent)
                digest = _file_digest(source)
                verify_artifact_roundtrip(
                    mlflow_client,
                    run_id,
                    remote,
                    digest,
                    output / "roundtrip" / source.name,
                )
                manifest.append({"path": remote, "sha256": digest, "size_bytes": source.stat().st_size})
            if role != "evaluate":
                roundtrip_model = Path(
                    mlflow_client.download_artifacts(run_id, "model", str(output / "roundtrip-model"))
                )
                _verify_fresh_adapter_load(roundtrip_model)
            evidence = {
                "schema_version": "galatea.evidence/v1",
                "lineage": {
                    "project_id": binding["project_id"],
                    "campaign_id": binding["campaign_id"],
                    "operation_id": binding["operation_id"],
                    "submission_id": binding["submission_id"],
                    "config_id": binding["config_id"],
                    "config_digest": binding["config_digest"],
                    "release_id": binding["release_id"],
                    "release_digest": binding["release_digest"],
                    "dataset_digest": binding["dataset_digest"],
                    "split_digest": binding["split_digest"],
                    "preprocessing": binding["preprocessing"],
                    "metric_definition": binding["metric_definition"],
                    "evaluation_protocol": binding["evaluation_protocol"],
                    "seed": binding["seed"],
                    "role": role,
                    "readiness_digest": binding["readiness_digest"],
                    "clean_start": bool(binding["clean_start"]),
                    "candidate_id": binding.get("candidate_id"),
                    "champion_run_id": binding.get("champion_run_id"),
                    **(
                        {"model_sha256": binding["champion_model_sha256"]}
                        if role == "evaluate"
                        else {}
                    ),
                },
                "artifacts": manifest,
                "integrity": {"roundtrip": True, "load_verified": True},
                "metrics": metrics,
                "final_test_status": "passed" if role == "evaluate" else "not-run",
                "wall_seconds": time.monotonic() - started,
            }
            evidence_path = output / "evidence.json"
            evidence_path.write_text(json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8")
            mlflow_client.log_artifact(run_id, str(evidence_path), "reports")
            verify_artifact_roundtrip(
                mlflow_client,
                run_id,
                "reports/evidence.json",
                _file_digest(evidence_path),
                output / "roundtrip" / "evidence",
            )
        mlflow_client.set_tag(run_id, "artifact.roundtrip_verified", "true")
        mlflow_client.set_tag(run_id, "run.outcome", "succeeded")
        mlflow_client.set_tag(
            run_id,
            "model.uri",
            f"runs:/{run_id}/model",
        )
        mlflow_client.set_terminated(run_id, status="FINISHED")
        return {"status": "succeeded", "run_id": run_id, "role": role, "metrics": metrics}
    except BaseException:
        mlflow_client.set_tag(run_id, "run.outcome", "failed")
        mlflow_client.set_terminated(run_id, status="FAILED")
        raise
