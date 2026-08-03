"""Ray Train PyTorch worker loop; workers never write directly to MLflow."""

from __future__ import annotations

import json
import math
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any


def _is_better(value: float, best: float, mode: str) -> bool:
    return value > best if mode == "max" else value < best


def _optimizer(model: Any, training: dict[str, Any]) -> Any:
    import torch

    optimizer_class = {
        "adam": torch.optim.Adam,
        "rmsprop": torch.optim.RMSprop,
    }[training["optimizer"]]
    return optimizer_class(model.parameters(), lr=float(training["learning_rate"]))


def _global_epoch_metrics(
    loss_sum: float,
    confusion: Any,
    device: Any,
) -> dict[str, float]:
    import torch
    import torch.distributed as dist

    totals = torch.cat(
        (
            torch.tensor(
                [loss_sum],
                dtype=torch.float64,
                device=device,
            ),
            confusion.to(device=device, dtype=torch.float64).reshape(-1),
        )
    )
    if dist.is_available() and dist.is_initialized():
        dist.all_reduce(totals, op=dist.ReduceOp.SUM)
    total_loss = float(totals[0].item())
    matrix = totals[1:].reshape_as(confusion).cpu()
    total_count = float(matrix.sum().item())
    correct = float(matrix.diag().sum().item())

    row_totals = matrix.sum(dim=1)
    column_totals = matrix.sum(dim=0)
    true_positive = matrix.diag()
    per_class_precision = true_positive / torch.clamp(column_totals, min=1.0)
    per_class_recall = true_positive / torch.clamp(row_totals, min=1.0)
    per_class_f1 = (
        2.0 * per_class_precision * per_class_recall
        / torch.clamp(per_class_precision + per_class_recall, min=1e-12)
    )
    positive_precision = float(per_class_precision[-1].item())
    positive_recall = float(per_class_recall[-1].item())
    positive_f1 = float(per_class_f1[-1].item())
    return {
        "loss": total_loss / max(1.0, total_count),
        "accuracy": correct / max(1.0, total_count),
        "precision": positive_precision,
        "recall": positive_recall,
        "f1": positive_f1,
        "cat_precision": float(per_class_precision[0].item()),
        "cat_recall": float(per_class_recall[0].item()),
        "cat_f1": float(per_class_f1[0].item()),
        "dog_precision": positive_precision,
        "dog_recall": positive_recall,
        "dog_f1": positive_f1,
        "macro_precision": float(per_class_precision.mean().item()),
        "macro_recall": float(per_class_recall.mean().item()),
        "macro_f1": float(per_class_f1.mean().item()),
        "examples": total_count,
    }


def _augment_batch(images: Any) -> Any:
    """Apply independent affine augmentation to a whole batch on the GPU."""

    import torch
    import torch.nn.functional as functional

    batch_size = len(images)
    flip_mask = torch.rand(batch_size, device=images.device) < 0.5
    images = torch.where(
        flip_mask.view(-1, 1, 1, 1),
        images.flip(-1),
        images,
    )

    angles = (torch.rand(batch_size, device=images.device) * 40.0 - 20.0).deg2rad()
    translations = torch.rand(batch_size, 2, device=images.device) * 0.4 - 0.2
    cosine = angles.cos()
    sine = angles.sin()
    theta = torch.zeros(
        (batch_size, 2, 3),
        dtype=images.dtype,
        device=images.device,
    )
    theta[:, 0, 0] = cosine
    theta[:, 0, 1] = -sine
    theta[:, 1, 0] = sine
    theta[:, 1, 1] = cosine
    theta[:, :, 2] = translations
    grid = functional.affine_grid(theta, images.shape, align_corners=False)
    return functional.grid_sample(
        images,
        grid,
        mode="bilinear",
        padding_mode="zeros",
        align_corners=False,
    )


def _prepare_images(images: Any, device: Any, *, augmentation: bool) -> Any:
    import torch

    is_uint8 = images.dtype == torch.uint8
    images = images.to(
        device,
        dtype=torch.float32,
        non_blocking=True,
    )
    if is_uint8:
        images.mul_(1.0 / 255.0)
    if augmentation:
        with torch.no_grad():
            images = _augment_batch(images)
    if device.type == "cuda":
        images = images.contiguous(memory_format=torch.channels_last)
    return images


def _run_epoch(
    model: Any,
    batches: Any,
    criterion: Any,
    device: Any,
    optimizer: Any | None,
    *,
    show_progress: bool,
    augmentation: bool = False,
    mixed_precision: str = "none",
    total_batches: int | None = None,
    progress_description: str | None = None,
) -> dict[str, float]:
    import torch
    from tqdm import tqdm

    training = optimizer is not None
    model.train(training)
    loss_sum = 0.0
    confusion = None
    batch_count = 0
    local_count = 0
    local_correct = 0
    data_wait_seconds = 0.0

    def measured_batches() -> Any:
        nonlocal data_wait_seconds
        iterator = iter(batches)
        while True:
            wait_started_at = time.perf_counter()
            try:
                batch = next(iterator)
            except StopIteration:
                break
            data_wait_seconds += time.perf_counter() - wait_started_at
            yield batch

    progress = tqdm(
        measured_batches(),
        desc=progress_description
        or ("train batches" if training else "validation batches"),
        unit="batch",
        total=total_batches,
        leave=True,
        mininterval=1.0,
        disable=not show_progress,
    )
    for images, labels in progress:
        images = _prepare_images(
            images,
            device,
            augmentation=training and augmentation,
        )
        labels = labels.to(device, non_blocking=True)
        if training:
            optimizer.zero_grad(set_to_none=True)
        with torch.set_grad_enabled(training), torch.autocast(
            device_type=device.type,
            dtype=torch.bfloat16,
            enabled=device.type == "cuda" and mixed_precision == "bf16",
        ):
            logits = model(images)
            loss = criterion(logits, labels)
            if training:
                if not torch.isfinite(loss):
                    raise FloatingPointError("Non-finite distributed training loss")
                loss.backward()
                optimizer.step()
        loss_sum += float(loss.item()) * len(labels)
        predictions = logits.argmax(dim=1)
        if show_progress:
            local_count += len(labels)
            local_correct += int((predictions == labels).sum().item())
        if confusion is None:
            confusion = torch.zeros(
                (logits.shape[1], logits.shape[1]),
                dtype=torch.float64,
                device=device,
            )
        confusion.index_put_(
            (labels, predictions),
            torch.ones_like(labels, dtype=torch.float64),
            accumulate=True,
        )
        batch_count += 1
        if show_progress:
            progress.set_postfix(
                loss=f"{loss_sum / local_count:.4f}",
                accuracy=f"{local_correct / local_count:.3f}",
                refresh=False,
            )
    if confusion is None:
        raise RuntimeError("Ray worker received an empty dataset shard")
    result = _global_epoch_metrics(loss_sum, confusion, device)
    result["batches"] = float(batch_count)
    result["data_wait_seconds"] = data_wait_seconds
    return result


def _unwrapped_model(model: Any) -> Any:
    return model.module if hasattr(model, "module") else model


def train_loop_per_worker(loop_config: dict[str, Any]) -> None:
    import torch
    from ray import train
    from ray.train import Checkpoint
    from ray.train.torch import get_device, prepare_model

    from ray_cats_dogs.input_pipeline import make_worker_dataset
    from ray_cats_dogs.models import build_model

    context = train.get_context()
    rank = context.get_world_rank()
    world_size = context.get_world_size()
    seed = int(loop_config["seed"])
    torch.manual_seed(seed + rank)
    if torch.cuda.is_available():
        if not (torch.version.cuda or "").startswith("13."):
            raise RuntimeError(
                "Ray worker loaded a non-CUDA-13 PyTorch build; install the project "
                "torch==2.11.0 CUDA 13 environment before training."
            )
        torch.cuda.manual_seed_all(seed + rank)
    device = get_device()
    if (
        device.type == "cuda"
        and loop_config["training"]["mixed_precision"] == "bf16"
        and not torch.cuda.is_bf16_supported()
    ):
        raise RuntimeError("BF16 training requires a CUDA device with BF16 support")

    train_dataset = make_worker_dataset(
        train.get_dataset_shard("training"),
        batch_size=int(loop_config["training"]["per_worker_batch_size"]),
        prefetch_batches=int(loop_config["data_prefetch_batches"]),
    )
    validation_dataset = make_worker_dataset(
        train.get_dataset_shard("validation"),
        batch_size=int(loop_config["training"]["per_worker_batch_size"]),
        prefetch_batches=int(loop_config["data_prefetch_batches"]),
    )

    initial_best = -math.inf if loop_config["objective_mode"] == "max" else math.inf
    best_metric = initial_best
    best_epoch = 0
    no_improvement = 0
    starting_epoch = 0

    with tempfile.TemporaryDirectory(prefix="ray-cats-dogs-worker-") as state_directory:
        state_dir = Path(state_directory)
        checkpoint = train.get_checkpoint()
        if checkpoint is not None:
            with checkpoint.as_directory() as checkpoint_directory:
                shutil.copytree(checkpoint_directory, state_dir, dirs_exist_ok=True)
            state = json.loads(
                (state_dir / "training-state.json").read_text(encoding="utf-8")
            )
            starting_epoch = int(state["epoch"])
            best_metric = float(state["best_metric"])
            best_epoch = int(state["best_epoch"])
            no_improvement = int(state["no_improvement"])

        model = build_model(
            loop_config["model"],
            loop_config["training"],
            tuple(loop_config["image_size"]),
            seed,
        )
        optimizer = _optimizer(model, loop_config["training"])
        if checkpoint is not None:
            current = torch.load(
                state_dir / "current-model.pt",
                map_location="cpu",
                weights_only=False,
            )
            model.load_state_dict(current["model_state_dict"])
            optimizer.load_state_dict(current["optimizer_state_dict"])
        if device.type == "cuda":
            torch.backends.cudnn.benchmark = True
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            torch.set_float32_matmul_precision("high")
            model = model.to(memory_format=torch.channels_last)
        model = prepare_model(model)
        torch.manual_seed(seed + rank)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed + rank)
        criterion = torch.nn.CrossEntropyLoss()

        objective_name = loop_config["objective_metric"]
        total_epochs = int(loop_config["training"]["epochs"])
        for epoch in range(starting_epoch, total_epochs):
            started_at = time.perf_counter()
            train_started_at = time.perf_counter()
            train_epoch = _run_epoch(
                model,
                train_dataset,
                criterion,
                device,
                optimizer,
                show_progress=rank == 0,
                augmentation=bool(loop_config["model"]["augmentation"]),
                mixed_precision=str(loop_config["training"]["mixed_precision"]),
                total_batches=int(loop_config["training_batches_per_worker"]),
                progress_description=f"epoch {epoch + 1}/{total_epochs} train",
            )
            train_duration = time.perf_counter() - train_started_at
            validation_started_at = time.perf_counter()
            validation_epoch = _run_epoch(
                model,
                validation_dataset,
                criterion,
                device,
                None,
                show_progress=rank == 0,
                mixed_precision=str(loop_config["training"]["mixed_precision"]),
                total_batches=int(loop_config["validation_batches_per_worker"]),
                progress_description=f"epoch {epoch + 1}/{total_epochs} validation",
            )
            validation_duration = time.perf_counter() - validation_started_at
            values = {
                "val_accuracy": validation_epoch["accuracy"],
                "val_loss": validation_epoch["loss"],
            }
            objective_value = values[objective_name]
            improved = _is_better(
                objective_value, best_metric, loop_config["objective_mode"]
            )
            if improved:
                best_metric = objective_value
                best_epoch = epoch + 1
                no_improvement = 0
            else:
                no_improvement += 1

            metrics = {
                "epoch": epoch + 1,
                "train_loss": train_epoch["loss"],
                "train_accuracy": train_epoch["accuracy"],
                "train_precision": train_epoch["precision"],
                "train_recall": train_epoch["recall"],
                "train_f1": train_epoch["f1"],
                "train_cat_precision": train_epoch["cat_precision"],
                "train_cat_recall": train_epoch["cat_recall"],
                "train_cat_f1": train_epoch["cat_f1"],
                "train_dog_precision": train_epoch["dog_precision"],
                "train_dog_recall": train_epoch["dog_recall"],
                "train_dog_f1": train_epoch["dog_f1"],
                "train_macro_precision": train_epoch["macro_precision"],
                "train_macro_recall": train_epoch["macro_recall"],
                "train_macro_f1": train_epoch["macro_f1"],
                "train_examples": train_epoch["examples"],
                "train_batches_per_worker": train_epoch["batches"],
                "train_examples_per_second": train_epoch["examples"]
                / max(train_duration, 1e-12),
                "val_loss": validation_epoch["loss"],
                "val_accuracy": validation_epoch["accuracy"],
                "val_precision": validation_epoch["precision"],
                "val_recall": validation_epoch["recall"],
                "val_f1": validation_epoch["f1"],
                "val_cat_precision": validation_epoch["cat_precision"],
                "val_cat_recall": validation_epoch["cat_recall"],
                "val_cat_f1": validation_epoch["cat_f1"],
                "val_dog_precision": validation_epoch["dog_precision"],
                "val_dog_recall": validation_epoch["dog_recall"],
                "val_dog_f1": validation_epoch["dog_f1"],
                "val_macro_precision": validation_epoch["macro_precision"],
                "val_macro_recall": validation_epoch["macro_recall"],
                "val_macro_f1": validation_epoch["macro_f1"],
                "val_examples": validation_epoch["examples"],
                "val_batches_per_worker": validation_epoch["batches"],
                "val_examples_per_second": validation_epoch["examples"]
                / max(validation_duration, 1e-12),
                "best_objective": best_metric,
                "epoch_duration_seconds": time.perf_counter() - started_at,
                "train_duration_seconds": train_duration,
                "train_data_wait_seconds": train_epoch["data_wait_seconds"],
                "train_data_wait_fraction": train_epoch["data_wait_seconds"]
                / max(train_duration, 1e-12),
                "val_duration_seconds": validation_duration,
                "val_data_wait_seconds": validation_epoch["data_wait_seconds"],
                "val_data_wait_fraction": validation_epoch["data_wait_seconds"]
                / max(validation_duration, 1e-12),
                "learning_rate": float(optimizer.param_groups[0]["lr"]),
                "worker_rank": rank,
                "world_size": world_size,
            }
            reported_checkpoint = None
            if rank == 0:
                torch.save(
                    {
                        "model_state_dict": _unwrapped_model(model).state_dict(),
                        "optimizer_state_dict": optimizer.state_dict(),
                        "epoch": epoch + 1,
                        "model_config": loop_config["model"],
                        "training_config": loop_config["training"],
                        "image_size": loop_config["image_size"],
                        "seed": seed,
                    },
                    state_dir / "current-model.pt",
                )
                if improved:
                    shutil.copy2(
                        state_dir / "current-model.pt",
                        state_dir / "best-model.pt",
                    )
                (state_dir / "training-state.json").write_text(
                    json.dumps(
                        {
                            "epoch": epoch + 1,
                            "best_epoch": best_epoch,
                            "best_metric": best_metric,
                            "no_improvement": no_improvement,
                            "objective_metric": objective_name,
                            "objective_mode": loop_config["objective_mode"],
                            "mlflow_run_id": loop_config["mlflow_run_id"],
                            "idempotency_key": loop_config["idempotency_key"],
                        },
                        indent=2,
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
                reported_checkpoint = Checkpoint.from_directory(state_dir)
            train.report(metrics, checkpoint=reported_checkpoint)
            if rank == 0:
                print(
                    json.dumps(
                        {"event": "epoch-complete", **metrics},
                        sort_keys=True,
                    ),
                    flush=True,
                )
            patience = int(loop_config["training"]["early_stopping_patience"])
            if not improved and no_improvement >= max(1, patience):
                break
