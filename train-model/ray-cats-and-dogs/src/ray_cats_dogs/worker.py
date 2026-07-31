"""Ray Train worker loop; workers never write directly to MLflow."""

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


def train_loop_per_worker(loop_config: dict[str, Any]) -> None:
    import tensorflow as tf
    from ray import train
    from ray.train import Checkpoint

    from ray_cats_dogs.input_pipeline import make_worker_dataset
    from ray_cats_dogs.models import build_model

    context = train.get_context()
    rank = context.get_world_rank()
    world_size = context.get_world_size()
    seed = int(loop_config["seed"])
    tf.keras.utils.set_random_seed(seed)

    train_dataset = make_worker_dataset(
        train.get_dataset_shard("training"),
        image_size=tuple(loop_config["image_size"]),
        batch_size=int(loop_config["training"]["per_worker_batch_size"]),
        training=True,
        seed=seed + rank,
    )
    validation_dataset = make_worker_dataset(
        train.get_dataset_shard("validation"),
        image_size=tuple(loop_config["image_size"]),
        batch_size=int(loop_config["training"]["per_worker_batch_size"]),
        training=False,
        seed=seed,
    )

    strategy = tf.distribute.MultiWorkerMirroredStrategy()
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
            state = json.loads((state_dir / "training-state.json").read_text())
            starting_epoch = int(state["epoch"])
            best_metric = float(state["best_metric"])
            best_epoch = int(state["best_epoch"])
            no_improvement = int(state["no_improvement"])

        with strategy.scope():
            if checkpoint is None:
                model = build_model(
                    loop_config["model"],
                    loop_config["training"],
                    tuple(loop_config["image_size"]),
                    seed,
                )
            else:
                model = tf.keras.models.load_model(state_dir / "current-model.keras")

        objective_name = loop_config["objective_metric"]
        for epoch in range(starting_epoch, int(loop_config["training"]["epochs"])):
            started_at = time.perf_counter()
            history = model.fit(
                train_dataset,
                validation_data=validation_dataset,
                initial_epoch=epoch,
                epochs=epoch + 1,
                verbose=2 if rank == 0 else 0,
            )
            values = {name: float(series[-1]) for name, series in history.history.items()}
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
                "train_loss": values["loss"],
                "train_accuracy": values["accuracy"],
                "val_loss": values["val_loss"],
                "val_accuracy": values["val_accuracy"],
                "best_objective": best_metric,
                "epoch_duration_seconds": time.perf_counter() - started_at,
                "learning_rate": float(
                    tf.keras.backend.get_value(model.optimizer.learning_rate)
                ),
                "worker_rank": rank,
                "world_size": world_size,
            }
            reported_checkpoint = None
            if rank == 0:
                model.save(state_dir / "current-model.keras", overwrite=True)
                if improved:
                    shutil.copy2(
                        state_dir / "current-model.keras",
                        state_dir / "best-model.keras",
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
