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
    correct: int,
    count: int,
    device: Any,
) -> tuple[float, float]:
    import torch
    import torch.distributed as dist

    totals = torch.tensor(
        [loss_sum, float(correct), float(count)],
        dtype=torch.float64,
        device=device,
    )
    if dist.is_available() and dist.is_initialized():
        dist.all_reduce(totals, op=dist.ReduceOp.SUM)
    total_loss, total_correct, total_count = totals.cpu().tolist()
    return total_loss / max(1.0, total_count), total_correct / max(1.0, total_count)


def _run_epoch(
    model: Any,
    batches: Any,
    criterion: Any,
    device: Any,
    optimizer: Any | None,
) -> tuple[float, float]:
    import torch

    training = optimizer is not None
    model.train(training)
    loss_sum = 0.0
    correct = 0
    count = 0
    for images, labels in batches:
        images = images.to(device, non_blocking=True)
        labels = labels.to(device, non_blocking=True)
        if training:
            optimizer.zero_grad(set_to_none=True)
        with torch.set_grad_enabled(training):
            logits = model(images)
            loss = criterion(logits, labels)
            if training:
                if not torch.isfinite(loss):
                    raise FloatingPointError("Non-finite distributed training loss")
                loss.backward()
                optimizer.step()
        loss_sum += float(loss.item()) * len(labels)
        correct += int((logits.argmax(dim=1) == labels).sum().item())
        count += len(labels)
    return _global_epoch_metrics(loss_sum, correct, count, device)


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

    train_dataset = make_worker_dataset(
        train.get_dataset_shard("training"),
        image_size=tuple(loop_config["image_size"]),
        batch_size=int(loop_config["training"]["per_worker_batch_size"]),
        training=True,
        augmentation=bool(loop_config["model"]["augmentation"]),
        seed=seed + rank,
    )
    validation_dataset = make_worker_dataset(
        train.get_dataset_shard("validation"),
        image_size=tuple(loop_config["image_size"]),
        batch_size=int(loop_config["training"]["per_worker_batch_size"]),
        training=False,
        augmentation=False,
        seed=seed,
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
        model = prepare_model(model)
        criterion = torch.nn.CrossEntropyLoss()

        objective_name = loop_config["objective_metric"]
        for epoch in range(starting_epoch, int(loop_config["training"]["epochs"])):
            started_at = time.perf_counter()
            train_loss, train_accuracy = _run_epoch(
                model, train_dataset, criterion, device, optimizer
            )
            val_loss, val_accuracy = _run_epoch(
                model, validation_dataset, criterion, device, None
            )
            values = {
                "val_accuracy": val_accuracy,
                "val_loss": val_loss,
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
                "train_loss": train_loss,
                "train_accuracy": train_accuracy,
                "val_loss": val_loss,
                "val_accuracy": val_accuracy,
                "best_objective": best_metric,
                "epoch_duration_seconds": time.perf_counter() - started_at,
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
            patience = int(loop_config["training"]["early_stopping_patience"])
            if not improved and no_improvement >= max(1, patience):
                break
