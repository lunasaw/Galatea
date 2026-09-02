"""Ray Train 手写数字 worker 循环；worker 不直接写 MLflow。"""

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
    return {"adam": torch.optim.Adam, "rmsprop": torch.optim.RMSprop}[training["optimizer"]](model.parameters(), lr=float(training["learning_rate"]))


def _global_epoch_metrics(loss_sum: float, confusion: Any, device: Any) -> dict[str, float]:
    import torch
    import torch.distributed as dist
    totals = torch.cat((torch.tensor([loss_sum], dtype=torch.float64, device=device), confusion.to(device=device, dtype=torch.float64).reshape(-1)))
    if dist.is_available() and dist.is_initialized():
        dist.all_reduce(totals, op=dist.ReduceOp.SUM)
    total_loss = float(totals[0].item())
    matrix = totals[1:].reshape_as(confusion).cpu()
    total_count = float(matrix.sum().item())
    correct = float(matrix.diag().sum().item())
    row_totals = matrix.sum(dim=1)
    column_totals = matrix.sum(dim=0)
    true_positive = matrix.diag()
    precision = true_positive / torch.clamp(column_totals, min=1.0)
    recall = true_positive / torch.clamp(row_totals, min=1.0)
    f1 = 2.0 * precision * recall / torch.clamp(precision + recall, min=1e-12)
    return {"loss": total_loss / max(total_count, 1.0), "accuracy": correct / max(total_count, 1.0), "precision": float(precision.mean().item()), "recall": float(recall.mean().item()), "f1": float(f1.mean().item()), "macro_precision": float(precision.mean().item()), "macro_recall": float(recall.mean().item()), "macro_f1": float(f1.mean().item()), "examples": total_count}


def _augment_batch(images: Any) -> Any:
    import torch
    import torch.nn.functional as functional
    batch_size = len(images)
    angles = (torch.rand(batch_size, device=images.device) * 20.0 - 10.0).deg2rad()
    translations = torch.rand(batch_size, 2, device=images.device) * 0.12 - 0.06
    cosine, sine = angles.cos(), angles.sin()
    theta = torch.zeros((batch_size, 2, 3), dtype=images.dtype, device=images.device)
    theta[:, 0, 0], theta[:, 0, 1] = cosine, -sine
    theta[:, 1, 0], theta[:, 1, 1] = sine, cosine
    theta[:, :, 2] = translations
    return functional.grid_sample(images, functional.affine_grid(theta, images.shape, align_corners=False), mode="bilinear", padding_mode="zeros", align_corners=False)


def _prepare_images(images: Any, device: Any, *, augmentation: bool) -> Any:
    import torch
    is_uint8 = images.dtype == torch.uint8
    images = images.to(device, dtype=torch.float32, non_blocking=True)
    if is_uint8:
        images.mul_(1.0 / 255.0)
    # Kaggle 图片是白底黑色笔画；反相后与零填充增强保持一致。
    images = 1.0 - images
    if augmentation:
        with torch.no_grad():
            images = _augment_batch(images)
    return images.contiguous(memory_format=torch.channels_last) if device.type == "cuda" else images


def _run_epoch(model: Any, batches: Any, criterion: Any, device: Any, optimizer: Any | None, *, show_progress: bool, augmentation: bool = False, mixed_precision: str = "none", total_batches: int | None = None, progress_description: str | None = None) -> dict[str, float]:
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
    iterator = iter(batches)
    progress = tqdm(range(total_batches or 0), desc=progress_description or "batches", unit="batch", total=total_batches, leave=True, mininterval=1.0, disable=not show_progress)
    for _ in progress:
        wait_started_at = time.perf_counter()
        try:
            images, labels = next(iterator)
        except StopIteration:
            break
        data_wait_seconds += time.perf_counter() - wait_started_at
        images = _prepare_images(images, device, augmentation=training and augmentation)
        labels = labels.to(device, non_blocking=True)
        if training:
            optimizer.zero_grad(set_to_none=True)
        with torch.set_grad_enabled(training), torch.autocast(device_type=device.type, dtype=torch.bfloat16, enabled=device.type == "cuda" and mixed_precision == "bf16"):
            logits = model(images)
            loss = criterion(logits, labels)
            if training:
                loss.backward(); optimizer.step()
        loss_sum += float(loss.item()) * len(labels)
        predictions = logits.argmax(dim=1)
        local_count += len(labels); local_correct += int((predictions == labels).sum().item())
        if confusion is None:
            confusion = torch.zeros((logits.shape[1], logits.shape[1]), dtype=torch.float64, device=device)
        confusion.index_put_((labels, predictions), torch.ones_like(labels, dtype=torch.float64), accumulate=True)
        batch_count += 1
        if show_progress:
            progress.set_postfix(loss=f"{loss_sum / max(local_count, 1):.4f}", accuracy=f"{local_correct / max(local_count, 1):.3f}", refresh=False)
    if confusion is None:
        raise RuntimeError("Ray worker received an empty dataset shard")
    result = _global_epoch_metrics(loss_sum, confusion, device)
    result.update({"batches": float(batch_count), "data_wait_seconds": data_wait_seconds})
    return result


def _unwrapped_model(model: Any) -> Any:
    return model.module if hasattr(model, "module") else model


def train_loop_per_worker(loop_config: dict[str, Any]) -> None:
    import torch
    from ray import train
    from ray.train import Checkpoint
    from ray.train.torch import get_device, prepare_model
    from ray_handwritten_digits.input_pipeline import make_worker_dataset
    from ray_handwritten_digits.models import build_model
    context = train.get_context(); rank = context.get_world_rank(); world_size = context.get_world_size(); seed = int(loop_config["seed"])
    torch.manual_seed(seed + rank)
    device = get_device()
    train_dataset = make_worker_dataset(train.get_dataset_shard("training"), batch_size=int(loop_config["training"]["per_worker_batch_size"]), prefetch_batches=int(loop_config["data_prefetch_batches"]))
    validation_dataset = make_worker_dataset(train.get_dataset_shard("validation"), batch_size=int(loop_config["training"]["per_worker_batch_size"]), prefetch_batches=int(loop_config["data_prefetch_batches"]))
    model = build_model(loop_config["model"], loop_config["training"], tuple(loop_config["image_size"]), seed)
    optimizer = _optimizer(model, loop_config["training"])
    if device.type == "cuda":
        model = model.to(memory_format=torch.channels_last)
    model = prepare_model(model)
    criterion = torch.nn.CrossEntropyLoss()
    best_metric = -math.inf; best_epoch = 0; no_improvement = 0; starting_epoch = 0
    with tempfile.TemporaryDirectory(prefix="ray-handwritten-digits-worker-") as state_directory:
        state_dir = Path(state_directory)
        checkpoint = train.get_checkpoint()
        if checkpoint is not None:
            with checkpoint.as_directory() as checkpoint_directory: shutil.copytree(checkpoint_directory, state_dir, dirs_exist_ok=True)
            state = json.loads((state_dir / "training-state.json").read_text(encoding="utf-8")); starting_epoch = int(state["epoch"]); best_metric = float(state["best_metric"]); best_epoch = int(state["best_epoch"]); no_improvement = int(state["no_improvement"])
            current = torch.load(state_dir / "current-model.pt", map_location="cpu", weights_only=False); model.load_state_dict(current["model_state_dict"]); optimizer.load_state_dict(current["optimizer_state_dict"])
        for epoch in range(starting_epoch, int(loop_config["training"]["epochs"])):
            started_at = time.perf_counter()
            train_epoch = _run_epoch(model, train_dataset, criterion, device, optimizer, show_progress=rank == 0, augmentation=bool(loop_config["model"]["augmentation"]), mixed_precision=str(loop_config["training"]["mixed_precision"]), total_batches=int(loop_config["training_batches_per_worker"]), progress_description=f"epoch {epoch + 1} train")
            validation_epoch = _run_epoch(model, validation_dataset, criterion, device, None, show_progress=rank == 0, mixed_precision=str(loop_config["training"]["mixed_precision"]), total_batches=int(loop_config["validation_batches_per_worker"]), progress_description=f"epoch {epoch + 1} validation")
            objective_value = validation_epoch["accuracy"] if loop_config["objective_metric"] == "val_accuracy" else validation_epoch["loss"]
            improved = _is_better(objective_value, best_metric, loop_config["objective_mode"])
            if improved: best_metric = objective_value; best_epoch = epoch + 1; no_improvement = 0
            else: no_improvement += 1
            metrics = {"epoch": epoch + 1, "train_loss": train_epoch["loss"], "train_accuracy": train_epoch["accuracy"], "train_precision": train_epoch["precision"], "train_recall": train_epoch["recall"], "train_f1": train_epoch["f1"], "train_macro_precision": train_epoch["macro_precision"], "train_macro_recall": train_epoch["macro_recall"], "train_macro_f1": train_epoch["macro_f1"], "train_examples": train_epoch["examples"], "train_batches_per_worker": train_epoch["batches"], "val_loss": validation_epoch["loss"], "val_accuracy": validation_epoch["accuracy"], "val_precision": validation_epoch["precision"], "val_recall": validation_epoch["recall"], "val_f1": validation_epoch["f1"], "val_macro_precision": validation_epoch["macro_precision"], "val_macro_recall": validation_epoch["macro_recall"], "val_macro_f1": validation_epoch["macro_f1"], "val_examples": validation_epoch["examples"], "val_batches_per_worker": validation_epoch["batches"], "best_objective": best_metric, "epoch_duration_seconds": time.perf_counter() - started_at, "worker_rank": rank, "world_size": world_size}
            reported_checkpoint = None
            if rank == 0:
                torch.save({"model_state_dict": _unwrapped_model(model).state_dict(), "optimizer_state_dict": optimizer.state_dict(), "epoch": epoch + 1, "model_config": loop_config["model"], "training_config": loop_config["training"], "image_size": loop_config["image_size"], "seed": seed}, state_dir / "current-model.pt")
                if improved: shutil.copy2(state_dir / "current-model.pt", state_dir / "best-model.pt")
                (state_dir / "training-state.json").write_text(json.dumps({"epoch": epoch + 1, "best_epoch": best_epoch, "best_metric": best_metric, "no_improvement": no_improvement, "objective_metric": loop_config["objective_metric"], "objective_mode": loop_config["objective_mode"], "mlflow_run_id": loop_config["mlflow_run_id"], "idempotency_key": loop_config["idempotency_key"]}, indent=2, sort_keys=True), encoding="utf-8")
                reported_checkpoint = Checkpoint.from_directory(state_dir)
            train.report(metrics, checkpoint=reported_checkpoint)
            if not improved and no_improvement >= max(1, int(loop_config["training"]["early_stopping_patience"])): break
