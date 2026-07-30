"""Reusable training and MLflow tracking pipeline for the cats-vs-dogs notebook."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import shutil
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import mlflow
import mlflow.data
import mlflow.tensorflow
import numpy as np
import pandas as pd
import tensorflow as tf
from mlflow import MlflowClient
from mlflow.models import infer_signature
from PIL import Image, UnidentifiedImageError
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from tensorflow.keras.models import Model
from tensorflow.keras.preprocessing.image import ImageDataGenerator


CLASS_NAMES = ("Cat", "Dog")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
MODULE_PATH = Path(__file__).resolve()

# Model logging must not infer or persist unrelated environment-variable names.
os.environ.setdefault("MLFLOW_RECORD_ENV_VARS_IN_MODEL_LOGGING", "false")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() == "true"


@dataclass(frozen=True)
class PipelineConfig:
    """Configuration shared by data preparation and both tracked training Runs."""

    repo_root: Path
    data_dir: Path
    split_root: Path
    notebook_path: Path
    tracking_uri: str = "http://127.0.0.1:5000"
    experiment_name: str = "cats-vs-dogs-enterprise"
    dataset_source_uri: str | None = None
    dataset_version_override: str | None = None
    run_group_id: str = ""
    seed: int = 42
    epochs: int = 1
    image_size: tuple[int, int] = (150, 150)
    baseline_batch_size: int = 64
    augmented_batch_size: int = 32
    learning_rate: float = 0.001
    train_fraction: float = 0.90
    early_stopping_patience: int = 3
    min_test_accuracy: float = 0.80
    require_remote_artifact_store: bool = True
    expected_images_per_class: int | None = 12500
    expected_valid_images: int | None = 24998

    def __post_init__(self) -> None:
        if not 0 < self.train_fraction < 1:
            raise ValueError("train_fraction must be between 0 and 1")
        if self.epochs < 1:
            raise ValueError("epochs must be at least 1")
        if self.baseline_batch_size < 1 or self.augmented_batch_size < 1:
            raise ValueError("batch sizes must be positive")
        if self.learning_rate <= 0:
            raise ValueError("learning_rate must be positive")
        if not 0 <= self.min_test_accuracy <= 1:
            raise ValueError("min_test_accuracy must be between 0 and 1")
        if any(character in self.run_group_id for character in ("'", '"')):
            raise ValueError("run_group_id cannot contain quotes")

    @property
    def pet_images_dir(self) -> Path:
        return self.data_dir / "PetImages"

    @property
    def source_uri(self) -> str:
        return self.dataset_source_uri or self.pet_images_dir.resolve().as_uri()

    @classmethod
    def from_env(cls) -> "PipelineConfig":
        repo_root = Path(
            os.getenv("TRAIN_REPO_ROOT", "/data/ai/chenzhangyue/code/train")
        ).resolve()
        run_group_id = os.getenv(
            "MLFLOW_RUN_GROUP_ID",
            f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}",
        )
        return cls(
            repo_root=repo_root,
            data_dir=Path(
                os.getenv(
                    "CATS_DOGS_DATA_DIR",
                    "/data/ai/chenzhangyue/code/data/cats-and-dogs",
                )
            ).resolve(),
            split_root=Path(
                os.getenv("CATS_DOGS_SPLIT_ROOT", "/tmp/cats-v-dogs")
            ).resolve(),
            notebook_path=(
                repo_root
                / "train-model/cats-and-dogs/cats-vs-dogs-classification.ipynb"
            ),
            tracking_uri=os.getenv(
                "MLFLOW_TRACKING_URI", "http://127.0.0.1:5000"
            ),
            experiment_name=os.getenv(
                "MLFLOW_EXPERIMENT_NAME", "cats-vs-dogs-enterprise"
            ),
            dataset_source_uri=os.getenv("CATS_DOGS_DATASET_SOURCE_URI"),
            dataset_version_override=os.getenv("CATS_DOGS_DATASET_VERSION"),
            run_group_id=run_group_id,
            epochs=int(os.getenv("CATS_DOGS_EPOCHS", "1")),
            min_test_accuracy=float(
                os.getenv("CATS_DOGS_MIN_TEST_ACCURACY", "0.80")
            ),
            require_remote_artifact_store=_env_bool(
                "MLFLOW_REQUIRE_REMOTE_ARTIFACT_STORE", True
            ),
        )


@dataclass(frozen=True)
class TrackingContext:
    experiment_id: str
    experiment_name: str
    artifact_location: str


@dataclass
class PreparedDataset:
    manifest: pd.DataFrame
    invalid_files: list[dict[str, str]]
    dataset_version: str
    content_digest: str
    split_digest: str
    source_uri: str
    manifest_path: Path
    split_root: Path
    profile: dict[str, Any]

    @property
    def training_dir(self) -> Path:
        return self.split_root / "training"

    @property
    def validation_dir(self) -> Path:
        return self.split_root / "validation"

    @property
    def test_dir(self) -> Path:
        return self.split_root / "test"

    @property
    def split_counts(self) -> pd.DataFrame:
        return self.manifest.groupby(["split", "class_name"]).size().unstack(fill_value=0)


@dataclass(frozen=True)
class DataGenerators:
    training: Any
    validation: Any
    test: Any
    batch_size: int
    augmentation: dict[str, Any]
    input_scaling: str = "pixel_value / 255.0"


@dataclass
class ExperimentResult:
    run_id: str
    model_uri: str | None
    artifact_uri: str
    model: Model
    history: pd.DataFrame
    test_metrics: dict[str, float]
    predictions: pd.DataFrame
    confusion_matrix: np.ndarray
    quality_gate_passed: bool | None


def _command_output(arguments: list[str], cwd: Path) -> str | None:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def preflight_tracking(config: PipelineConfig) -> TrackingContext:
    """Fail before data work when MLflow or the remote Artifact Store is unavailable."""

    mlflow.set_tracking_uri(config.tracking_uri)
    client = MlflowClient()
    try:
        client.search_experiments(max_results=1)
        experiment = mlflow.set_experiment(config.experiment_name)
    except Exception as error:
        raise ConnectionError(
            f"MLflow is unavailable at {config.tracking_uri}. Start mlflow.service "
            "and verify its /health endpoint before formal training."
        ) from error

    artifact_scheme = experiment.artifact_location.split(":", maxsplit=1)[0]
    if config.require_remote_artifact_store and artifact_scheme not in {
        "mlflow-artifacts",
        "s3",
    }:
        raise RuntimeError(
            "Experiment Artifact Store is not remote: "
            f"{experiment.artifact_location}. Expected the MLflow artifact proxy or s3:// MinIO."
        )

    return TrackingContext(
        experiment_id=experiment.experiment_id,
        experiment_name=experiment.name,
        artifact_location=experiment.artifact_location,
    )


def _safe_reset_split_root(split_root: Path) -> None:
    resolved = split_root.resolve()
    if resolved.parent != Path("/tmp") or not resolved.name.startswith("cats-v-dogs"):
        raise RuntimeError(f"Refusing to clean unexpected split path: {resolved}")
    shutil.rmtree(resolved, ignore_errors=True)
    for split_name in ("training", "validation", "test"):
        for class_name in ("cats", "dogs"):
            (resolved / split_name / class_name).mkdir(parents=True, exist_ok=True)


def _file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_and_split_class(
    config: PipelineConfig,
    source_dir: Path,
    output_class_name: str,
    label: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    valid_files: list[Path] = []
    invalid_files: list[dict[str, str]] = []
    for path in sorted(source_dir.iterdir()):
        if path.suffix.lower() not in IMAGE_SUFFIXES or path.stat().st_size == 0:
            continue
        try:
            with Image.open(path) as image:
                image.verify()
            valid_files.append(path)
        except (OSError, UnidentifiedImageError) as error:
            invalid_files.append(
                {
                    "relative_path": path.relative_to(config.pet_images_dir).as_posix(),
                    "error_type": type(error).__name__,
                }
            )

    random.Random(config.seed + label).shuffle(valid_files)
    train_end = int(config.train_fraction * len(valid_files))
    validation_end = train_end + (len(valid_files) - train_end) // 2
    split_files = {
        "training": valid_files[:train_end],
        "validation": valid_files[train_end:validation_end],
        "test": valid_files[validation_end:],
    }

    records: list[dict[str, Any]] = []
    for split_name, paths in split_files.items():
        destination = config.split_root / split_name / output_class_name
        for source_path in paths:
            checksum = _file_sha256(source_path)
            shutil.copy2(source_path, destination / source_path.name)
            records.append(
                {
                    "relative_path": source_path.relative_to(
                        config.pet_images_dir
                    ).as_posix(),
                    "split": split_name,
                    "class_name": CLASS_NAMES[label],
                    "label": label,
                    "bytes": source_path.stat().st_size,
                    "sha256": checksum,
                }
            )
    return records, invalid_files


def prepare_dataset(config: PipelineConfig) -> PreparedDataset:
    """Validate source images and create content-addressed deterministic splits."""

    cat_dir = config.pet_images_dir / "Cat"
    dog_dir = config.pet_images_dir / "Dog"
    if not cat_dir.is_dir() or not dog_dir.is_dir():
        raise FileNotFoundError(
            f"Expected extracted dataset directories at {cat_dir} and {dog_dir}"
        )

    source_counts = {
        "Cat": sum(path.suffix.lower() in IMAGE_SUFFIXES for path in cat_dir.iterdir()),
        "Dog": sum(path.suffix.lower() in IMAGE_SUFFIXES for path in dog_dir.iterdir()),
    }
    if config.expected_images_per_class is not None and tuple(source_counts.values()) != (
        config.expected_images_per_class,
        config.expected_images_per_class,
    ):
        raise RuntimeError(
            "Incomplete extraction: "
            + ", ".join(f"{name}={count}" for name, count in source_counts.items())
        )

    _safe_reset_split_root(config.split_root)
    cat_records, invalid_cat_files = _validate_and_split_class(
        config, cat_dir, "cats", label=0
    )
    dog_records, invalid_dog_files = _validate_and_split_class(
        config, dog_dir, "dogs", label=1
    )
    manifest = pd.DataFrame(cat_records + dog_records).sort_values(
        ["relative_path", "split"]
    ).reset_index(drop=True)
    invalid_files = invalid_cat_files + invalid_dog_files

    if manifest["relative_path"].duplicated().any():
        raise RuntimeError("The split manifest contains duplicate source images")
    if set(manifest["split"]) != {"training", "validation", "test"}:
        raise RuntimeError("The manifest is missing a required split")
    if config.expected_valid_images is not None and len(manifest) != config.expected_valid_images:
        raise RuntimeError(
            f"Expected {config.expected_valid_images:,} valid images; found {len(manifest):,}"
        )

    split_counts = manifest.groupby(["split", "class_name"]).size().unstack(fill_value=0)
    if split_counts.empty or (split_counts <= 0).any().any():
        raise RuntimeError("At least one split has an empty class")

    content_hasher = hashlib.sha256()
    split_hasher = hashlib.sha256()
    for record in manifest.to_dict(orient="records"):
        content_hasher.update(
            (
                f"{record['relative_path']}|{record['bytes']}|{record['sha256']}\n"
            ).encode()
        )
        split_hasher.update(
            f"{record['relative_path']}|{record['split']}\n".encode()
        )
    content_digest = content_hasher.hexdigest()
    split_digest = split_hasher.hexdigest()
    dataset_version = config.dataset_version_override or f"sha256-{content_digest[:16]}"
    manifest_path = config.split_root / "dataset-manifest.csv"
    manifest.to_csv(manifest_path, index=False)

    profile = {
        "dataset_name": "microsoft-cats-vs-dogs",
        "dataset_version": dataset_version,
        "source_uri": config.source_uri,
        "content_sha256": content_digest,
        "split_sha256": split_digest,
        "source_counts": source_counts,
        "valid_images": int(len(manifest)),
        "invalid_images": invalid_files,
        "total_bytes": int(manifest["bytes"].sum()),
        "split_counts": {
            split_name: {
                class_name: int(count)
                for class_name, count in class_counts.items()
            }
            for split_name, class_counts in split_counts.to_dict(orient="index").items()
        },
    }
    return PreparedDataset(
        manifest=manifest,
        invalid_files=invalid_files,
        dataset_version=dataset_version,
        content_digest=content_digest,
        split_digest=split_digest,
        source_uri=config.source_uri,
        manifest_path=manifest_path,
        split_root=config.split_root,
        profile=profile,
    )


def augmentation_policy(enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False}
    return {
        "enabled": True,
        "horizontal_flip": True,
        "rotation_range": 20,
        "width_shift_range": 0.2,
        "height_shift_range": 0.2,
        "fill_mode": "nearest",
    }


def create_generators(
    config: PipelineConfig,
    dataset: PreparedDataset,
    *,
    augmented: bool,
) -> DataGenerators:
    policy = augmentation_policy(augmented)
    batch_size = (
        config.augmented_batch_size if augmented else config.baseline_batch_size
    )
    if augmented:
        train_factory = ImageDataGenerator(
            rescale=1.0 / 255,
            horizontal_flip=policy["horizontal_flip"],
            rotation_range=policy["rotation_range"],
            width_shift_range=policy["width_shift_range"],
            height_shift_range=policy["height_shift_range"],
            fill_mode=policy["fill_mode"],
        )
    else:
        train_factory = ImageDataGenerator(rescale=1.0 / 255)
    evaluation_factory = ImageDataGenerator(rescale=1.0 / 255)

    common = {
        "target_size": config.image_size,
        "class_mode": "binary",
    }
    training = train_factory.flow_from_directory(
        str(dataset.training_dir),
        batch_size=batch_size,
        seed=config.seed,
        shuffle=True,
        **common,
    )
    validation = evaluation_factory.flow_from_directory(
        str(dataset.validation_dir),
        batch_size=batch_size,
        shuffle=False,
        **common,
    )
    test = evaluation_factory.flow_from_directory(
        str(dataset.test_dir),
        batch_size=batch_size,
        shuffle=False,
        **common,
    )
    return DataGenerators(
        training=training,
        validation=validation,
        test=test,
        batch_size=batch_size,
        augmentation=policy,
        input_scaling="pixel_value / 255.0",
    )


def build_model(config: PipelineConfig, variant: str) -> Model:
    if variant not in {"baseline", "augmented"}:
        raise ValueError(f"Unsupported model variant: {variant}")

    inputs = tf.keras.layers.Input(shape=(*config.image_size, 3))
    x = tf.keras.layers.Conv2D(32, (3, 3), activation="relu")(inputs)
    x = tf.keras.layers.Conv2D(64, (3, 3), activation="relu")(x)
    x = tf.keras.layers.MaxPooling2D(2, 2)(x)
    x = tf.keras.layers.Conv2D(64, (3, 3), activation="relu")(x)
    x = tf.keras.layers.Conv2D(128, (3, 3), activation="relu")(x)
    x = tf.keras.layers.MaxPooling2D(2, 2)(x)
    x = tf.keras.layers.Conv2D(128, (3, 3), activation="relu")(x)
    x = tf.keras.layers.Conv2D(256, (3, 3), activation="relu")(x)
    if variant == "augmented":
        x = tf.keras.layers.MaxPooling2D(2, 2)(x)
    x = tf.keras.layers.GlobalAveragePooling2D()(x)
    dense_units = 1024 if variant == "baseline" else 256
    x = tf.keras.layers.Dense(dense_units, activation="relu")(x)
    outputs = tf.keras.layers.Dense(len(CLASS_NAMES), activation="softmax")(x)

    model = Model(inputs=inputs, outputs=outputs, name=f"cats_dogs_{variant}_cnn")
    model.compile(
        optimizer=tf.keras.optimizers.RMSprop(learning_rate=config.learning_rate),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


class _EpochTelemetryCallback(tf.keras.callbacks.Callback):
    def on_epoch_begin(self, epoch: int, logs: dict | None = None) -> None:
        self.epoch_started_at = time.perf_counter()

    def on_epoch_end(self, epoch: int, logs: dict | None = None) -> None:
        logs = logs or {}
        metric_names = {
            "loss": "train_loss",
            "accuracy": "train_accuracy",
            "val_loss": "val_loss",
            "val_accuracy": "val_accuracy",
        }
        metrics = {
            metric_names[name]: float(value)
            for name, value in logs.items()
            if name in metric_names and np.isfinite(value)
        }
        metrics["epoch_duration_seconds"] = time.perf_counter() - self.epoch_started_at
        metrics["learning_rate"] = float(
            tf.keras.backend.get_value(self.model.optimizer.learning_rate)
        )
        mlflow.log_metrics(metrics, step=epoch)


def _split_digest(split_frame: pd.DataFrame) -> str:
    payload = split_frame[
        ["relative_path", "label", "bytes", "sha256"]
    ].to_csv(index=False).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _log_dataset_inputs(dataset: PreparedDataset) -> None:
    columns = ["relative_path", "class_name", "label", "bytes", "sha256"]
    for split_name in ("training", "validation", "test"):
        split_frame = dataset.manifest.loc[
            dataset.manifest["split"] == split_name, columns
        ].copy()
        mlflow_dataset = mlflow.data.from_pandas(
            split_frame,
            source=dataset.source_uri,
            targets="label",
            name=f"microsoft-cats-vs-dogs-{split_name}",
            digest=_split_digest(split_frame),
        )
        mlflow.log_input(mlflow_dataset, context=split_name)


def _evaluate_classifier(
    trained_model: Model,
    generator: Any,
    split_root: Path,
) -> tuple[dict[str, float], dict[str, Any], pd.DataFrame, np.ndarray]:
    generator.reset()
    evaluation = trained_model.evaluate(generator, verbose=0, return_dict=True)
    generator.reset()
    probabilities = trained_model.predict(generator, verbose=0)
    generator.reset()

    labels = generator.classes.astype("int32")
    predictions = np.argmax(probabilities, axis=1).astype("int32")
    metrics = {
        "test_loss": float(evaluation["loss"]),
        "test_accuracy": float(evaluation["accuracy"]),
        "test_precision": float(precision_score(labels, predictions, zero_division=0)),
        "test_recall": float(recall_score(labels, predictions, zero_division=0)),
        "test_f1": float(f1_score(labels, predictions, zero_division=0)),
        "test_roc_auc": float(roc_auc_score(labels, probabilities[:, 1])),
    }
    report = classification_report(
        labels,
        predictions,
        target_names=CLASS_NAMES,
        output_dict=True,
        zero_division=0,
    )
    predictions_frame = pd.DataFrame(
        {
            "relative_path": [
                Path(path).relative_to(split_root).as_posix()
                for path in generator.filepaths
            ],
            "actual_label": labels,
            "actual_class": [CLASS_NAMES[label] for label in labels],
            "predicted_label": predictions,
            "predicted_class": [CLASS_NAMES[label] for label in predictions],
            "probability_cat": probabilities[:, 0],
            "probability_dog": probabilities[:, 1],
        }
    )
    return (
        metrics,
        report,
        predictions_frame,
        confusion_matrix(labels, predictions),
    )


def _environment_report() -> dict[str, Any]:
    gpu_devices = []
    for device in tf.config.list_physical_devices("GPU"):
        try:
            details = tf.config.experimental.get_device_details(device)
            gpu_devices.append(details.get("device_name", device.name))
        except Exception:
            gpu_devices.append(device.name)
    return {
        "python": platform.python_version(),
        "tensorflow": tf.__version__,
        "mlflow": mlflow.__version__,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "operating_system": platform.platform(),
        "gpu_devices": gpu_devices,
    }


def plot_training_history(history: pd.DataFrame) -> plt.Figure:
    figure, axes = plt.subplots(1, 2, figsize=(13, 4))
    history.plot(x="epoch", y=["accuracy", "val_accuracy"], marker="o", ax=axes[0])
    history.plot(x="epoch", y=["loss", "val_loss"], marker="o", ax=axes[1])
    axes[0].set_title("Training and validation accuracy")
    axes[1].set_title("Training and validation loss")
    for axis in axes:
        axis.grid(alpha=0.25)
    figure.tight_layout()
    return figure


def plot_confusion_matrix(matrix: np.ndarray) -> plt.Figure:
    figure, axis = plt.subplots(figsize=(5, 4))
    image = axis.imshow(matrix, interpolation="nearest", cmap="Blues")
    figure.colorbar(image, ax=axis)
    axis.set(
        title="Test confusion matrix",
        xlabel="Predicted class",
        ylabel="Actual class",
        xticks=range(len(CLASS_NAMES)),
        yticks=range(len(CLASS_NAMES)),
        xticklabels=CLASS_NAMES,
        yticklabels=CLASS_NAMES,
    )
    threshold = matrix.max() / 2
    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            axis.text(
                column,
                row,
                str(matrix[row, column]),
                ha="center",
                va="center",
                color="white" if matrix[row, column] > threshold else "black",
            )
    figure.tight_layout()
    return figure


def _log_training_plots(
    history: pd.DataFrame,
    matrix: np.ndarray | None = None,
) -> None:
    figure = plot_training_history(history)
    mlflow.log_figure(figure, "plots/training-curves.png")
    plt.close(figure)
    if matrix is not None:
        figure = plot_confusion_matrix(matrix)
        mlflow.log_figure(figure, "plots/test-confusion-matrix.png")
        plt.close(figure)


def _run_parameters(
    config: PipelineConfig,
    dataset: PreparedDataset,
    generators: DataGenerators,
    model: Model,
    variant: str,
) -> dict[str, Any]:
    return {
        "model.variant": variant,
        "model.parameter_count": model.count_params(),
        "model.input_height": config.image_size[0],
        "model.input_width": config.image_size[1],
        "model.output_classes": len(CLASS_NAMES),
        "training.epochs_requested": config.epochs,
        "training.batch_size": generators.batch_size,
        "training.learning_rate": float(
            tf.keras.backend.get_value(model.optimizer.learning_rate)
        ),
        "training.optimizer": type(model.optimizer).__name__,
        "training.loss": "sparse_categorical_crossentropy",
        "training.seed": config.seed,
        "training.early_stopping_patience": config.early_stopping_patience,
        "training.input_scaling": generators.input_scaling,
        "data.dataset_version": dataset.dataset_version,
        "data.content_sha256": dataset.content_digest,
        "data.split_sha256": dataset.split_digest,
        "data.train_fraction": config.train_fraction,
        "data.training_images": int((dataset.manifest["split"] == "training").sum()),
        "data.validation_images": int(
            (dataset.manifest["split"] == "validation").sum()
        ),
        "data.test_images": int((dataset.manifest["split"] == "test").sum()),
        "quality.min_test_accuracy": config.min_test_accuracy,
        **{
            f"augmentation.{name}": value
            for name, value in generators.augmentation.items()
        },
    }


def run_tracked_training(
    config: PipelineConfig,
    tracking: TrackingContext,
    dataset: PreparedDataset,
    generators: DataGenerators,
    model: Model,
    *,
    variant: str,
    evaluate_test: bool = True,
    log_model: bool = True,
    selection_metric: str = "val_loss",
    extra_parameters: dict[str, Any] | None = None,
    extra_tags: dict[str, Any] | None = None,
    run_name_suffix: str = "",
    extra_source_paths: list[Path] | None = None,
) -> ExperimentResult:
    """Train and log one auditable Run, optionally reserving test evaluation."""

    if mlflow.active_run() is not None:
        raise RuntimeError("Close the active MLflow Run before starting a model variant")
    if variant not in {
        "baseline",
        "augmented",
        "smoke",
        "tuning-trial",
        "tuning-champion",
    }:
        raise ValueError(f"Unsupported tracked variant: {variant}")
    if selection_metric not in {"val_loss", "val_accuracy"}:
        raise ValueError(f"Unsupported selection metric: {selection_metric}")

    git_commit = _command_output(["git", "rev-parse", "HEAD"], config.repo_root)
    git_status = _command_output(["git", "status", "--porcelain"], config.repo_root)
    tags = {
        "project": "cats-vs-dogs",
        "run_group_id": config.run_group_id,
        "variant": variant,
        "dataset_version": dataset.dataset_version,
        "code.git_commit": git_commit or "uncommitted",
        "code.git_dirty": str(bool(git_status)).lower(),
        "execution.host": socket.gethostname(),
        "execution.type": "notebook",
        "lifecycle.stage": "development",
    }
    tags.update(extra_tags or {})
    parameters = _run_parameters(config, dataset, generators, model, variant)
    parameters.update(extra_parameters or {})
    run_name = f"{config.run_group_id}-{variant}{run_name_suffix}"
    phase = "run-setup"
    selection_mode = "min" if selection_metric == "val_loss" else "max"

    mlflow.set_tracking_uri(config.tracking_uri)
    with mlflow.start_run(
        experiment_id=tracking.experiment_id,
        run_name=run_name,
        tags=tags,
        description=(
            "TensorFlow cats-vs-dogs classifier with content-addressed data lineage "
            "and MinIO-backed artifacts."
        ),
        log_system_metrics=True,
    ) as active_run:
        run_id = active_run.info.run_id
        checkpoint_dir = dataset.split_root / "checkpoints" / run_id
        checkpoint_dir.mkdir(parents=True, exist_ok=False)
        checkpoint_path = checkpoint_dir / "best.keras"

        try:
            phase = "metadata-logging"
            mlflow.log_params(parameters)
            mlflow.log_dict(
                {
                    "parameters": parameters,
                    "augmentation": generators.augmentation,
                    "class_names": CLASS_NAMES,
                },
                "config/training-config.json",
            )
            mlflow.log_dict(dataset.profile, "data/dataset-profile.json")
            mlflow.log_artifact(str(dataset.manifest_path), artifact_path="data")
            mlflow.log_artifact(str(MODULE_PATH), artifact_path="source")
            if config.notebook_path.is_file():
                mlflow.log_artifact(str(config.notebook_path), artifact_path="source")
            for source_path in extra_source_paths or []:
                if source_path.is_file() and source_path.resolve() != MODULE_PATH:
                    mlflow.log_artifact(str(source_path), artifact_path="source")
            mlflow.log_dict(_environment_report(), "environment/runtime.json")
            _log_dataset_inputs(dataset)

            phase = "model-training"
            started_at = time.perf_counter()
            keras_history = model.fit(
                generators.training,
                epochs=config.epochs,
                validation_data=generators.validation,
                callbacks=[
                    _EpochTelemetryCallback(),
                    tf.keras.callbacks.TerminateOnNaN(),
                    tf.keras.callbacks.EarlyStopping(
                        monitor=selection_metric,
                        mode=selection_mode,
                        patience=config.early_stopping_patience,
                        restore_best_weights=True,
                    ),
                    tf.keras.callbacks.ModelCheckpoint(
                        filepath=str(checkpoint_path),
                        monitor=selection_metric,
                        mode=selection_mode,
                        save_best_only=True,
                    ),
                ],
            )
            training_seconds = time.perf_counter() - started_at

            phase = "model-evaluation"
            history = pd.DataFrame(keras_history.history)
            history.insert(0, "epoch", np.arange(1, len(history) + 1))
            if selection_mode == "min":
                best_epoch = int(history[selection_metric].idxmin()) + 1
            else:
                best_epoch = int(history[selection_metric].idxmax()) + 1

            final_metrics = {
                "training_duration_seconds": training_seconds,
                "epochs_completed": float(len(history)),
                "best_epoch": float(best_epoch),
                "best_val_loss": float(history["val_loss"].min()),
                "best_val_accuracy": float(history["val_accuracy"].max()),
            }
            test_metrics: dict[str, float] = {}
            predictions = pd.DataFrame()
            matrix = np.empty((0, 0), dtype="int64")
            quality_passed: bool | None = None
            output_digest: str | None = None
            if evaluate_test:
                test_metrics, report, predictions, matrix = _evaluate_classifier(
                    model, generators.test, dataset.split_root
                )
                output_digest = hashlib.sha256(
                    predictions.to_csv(index=False).encode()
                ).hexdigest()
                quality_passed = (
                    test_metrics["test_accuracy"] >= config.min_test_accuracy
                )
                final_metrics.update(test_metrics)
            mlflow.log_metrics(final_metrics)
            mlflow.log_table(history, "metrics/training-history.json")
            mlflow.log_dict(
                {
                    "selection_metric": selection_metric,
                    "selection_mode": selection_mode,
                    "best_epoch": best_epoch,
                    "best_val_loss": final_metrics["best_val_loss"],
                    "best_val_accuracy": final_metrics["best_val_accuracy"],
                    "test_evaluated": evaluate_test,
                },
                "reports/model-selection.json",
            )
            if evaluate_test:
                mlflow.log_table(predictions, "outputs/test-predictions.json")
                mlflow.log_dict(
                    {
                        "run_id": run_id,
                        "dataset_version": dataset.dataset_version,
                        "prediction_sha256": output_digest,
                        "metrics": test_metrics,
                        "classification_report": report,
                        "quality_gate": {
                            "minimum_test_accuracy": config.min_test_accuracy,
                            "passed": quality_passed,
                        },
                    },
                    "reports/evaluation.json",
                )
            _log_training_plots(history, matrix if evaluate_test else None)
            mlflow.log_artifact(str(checkpoint_path), artifact_path="checkpoints")

            model_uri: str | None = None
            if log_model:
                phase = "model-logging"
                example_generator = (
                    generators.test if evaluate_test else generators.validation
                )
                example_generator.reset()
                input_batch, _ = next(example_generator)
                example_generator.reset()
                input_example = input_batch[:4]
                output_example = model.predict(input_example, verbose=0)
                signature = infer_signature(input_example, output_example)
                metadata = {
                    "dataset_version": dataset.dataset_version,
                    "class_names": CLASS_NAMES,
                    "input_scaling": generators.input_scaling,
                }
                if output_digest is not None:
                    metadata["prediction_sha256"] = output_digest
                model_info = mlflow.tensorflow.log_model(
                    model,
                    name="model",
                    signature=signature,
                    input_example=input_example,
                    metadata=metadata,
                )
                model_uri = model_info.model_uri

            phase = "artifact-verification"
            verification_payload = {
                "run_id": run_id,
                "dataset_version": dataset.dataset_version,
                "artifact_uri": active_run.info.artifact_uri,
            }
            mlflow.log_dict(
                verification_payload, "verification/artifact-round-trip.json"
            )
            verification_dir = checkpoint_dir / "artifact-download"
            verification_dir.mkdir()
            downloaded_path = mlflow.artifacts.download_artifacts(
                run_id=run_id,
                artifact_path="verification/artifact-round-trip.json",
                dst_path=str(verification_dir),
            )
            with Path(downloaded_path).open(encoding="utf-8") as file_handle:
                downloaded_payload = json.load(file_handle)
            if downloaded_payload != verification_payload:
                raise RuntimeError("Artifact round-trip returned different content")

            outcome_tags = {
                "run.outcome": "succeeded",
                "artifact.roundtrip_verified": "true",
                "test.evaluated": str(evaluate_test).lower(),
            }
            if quality_passed is not None:
                outcome_tags["quality_gate.passed"] = str(quality_passed).lower()
            if output_digest is not None:
                outcome_tags["output.prediction_sha256"] = output_digest
            if model_uri is not None:
                outcome_tags["model.uri"] = model_uri
            mlflow.set_tags(outcome_tags)
            mlflow.flush_async_logging()
        except Exception as error:
            mlflow.set_tags(
                {
                    "run.outcome": "failed",
                    "failure.type": type(error).__name__,
                    "failure.phase": phase,
                }
            )
            raise

    return ExperimentResult(
        run_id=run_id,
        model_uri=model_uri,
        artifact_uri=active_run.info.artifact_uri,
        model=model,
        history=history,
        test_metrics=test_metrics,
        predictions=predictions,
        confusion_matrix=matrix,
        quality_gate_passed=quality_passed,
    )


def compare_run_group(
    config: PipelineConfig,
    tracking: TrackingContext,
) -> pd.DataFrame:
    mlflow.set_tracking_uri(config.tracking_uri)
    runs = mlflow.search_runs(
        experiment_ids=[tracking.experiment_id],
        filter_string=f"tags.run_group_id = '{config.run_group_id}'",
        order_by=["metrics.test_accuracy DESC"],
    )
    columns = [
        "run_id",
        "tags.variant",
        "status",
        "metrics.test_accuracy",
        "metrics.test_f1",
        "metrics.test_roc_auc",
        "metrics.best_val_loss",
        "metrics.training_duration_seconds",
        "tags.quality_gate.passed",
        "tags.artifact.roundtrip_verified",
        "tags.model.uri",
    ]
    return runs[[column for column in columns if column in runs.columns]]


def plot_dataset_distribution(dataset: PreparedDataset) -> plt.Figure:
    counts = dataset.manifest.groupby("class_name").size().reindex(CLASS_NAMES)
    figure, axis = plt.subplots(figsize=(5, 5))
    axis.pie(
        counts,
        labels=counts.index,
        autopct="%1.1f%%",
        colors=["#fad25a", "#e4572e"],
    )
    axis.set_title(f"Validated dataset ({int(counts.sum()):,} images)")
    return figure


def plot_image_batch(generator: Any, n_images: int = 9) -> plt.Figure:
    generator.reset()
    images, labels = next(generator)
    generator.reset()
    labels = labels.astype("int32")
    n_images = min(n_images, len(images))
    rows = int(np.ceil(n_images / 3))
    figure = plt.figure(figsize=(12, 4 * rows))
    for index, (image, label) in enumerate(
        zip(images[:n_images], labels[:n_images]), start=1
    ):
        axis = figure.add_subplot(rows, 3, index)
        axis.imshow(image)
        axis.set_title(CLASS_NAMES[label])
        axis.axis("off")
    figure.tight_layout()
    return figure


def plot_prediction_examples(
    result: ExperimentResult,
    generator: Any,
    n_images: int = 10,
) -> plt.Figure:
    generator.reset()
    images, labels = next(generator)
    generator.reset()
    probabilities = result.model.predict(images, verbose=0)
    predictions = np.argmax(probabilities, axis=1)
    labels = labels.astype("int32")
    n_images = min(n_images, len(images))
    rows = int(np.ceil(n_images / 3))
    figure = plt.figure(figsize=(12, 4 * rows))
    for index, (image, label) in enumerate(zip(images[:n_images], labels[:n_images])):
        axis = figure.add_subplot(rows, 3, index + 1)
        axis.imshow(image)
        predicted = predictions[index]
        color = "green" if predicted == label else "red"
        axis.set_title(
            f"Actual: {CLASS_NAMES[label]} | Predicted: {CLASS_NAMES[predicted]}",
            color=color,
        )
        axis.axis("off")
    figure.tight_layout()
    return figure


def plot_gradcam_examples(
    result: ExperimentResult,
    generator: Any,
    desired_class: int,
    n_images: int = 5,
) -> plt.Figure:
    if desired_class not in range(len(CLASS_NAMES)):
        raise ValueError(f"Invalid class index: {desired_class}")
    last_conv_layer = next(
        layer
        for layer in reversed(result.model.layers)
        if isinstance(layer, tf.keras.layers.Conv2D)
    )
    activation_model = Model(
        result.model.inputs, [last_conv_layer.output, result.model.output]
    )
    generator.reset()
    images, _ = next(generator)
    generator.reset()
    probabilities = result.model.predict(images, verbose=0)
    matching = np.flatnonzero(np.argmax(probabilities, axis=1) == desired_class)[:n_images]

    columns = max(1, len(matching))
    figure, axes = plt.subplots(1, columns, figsize=(4 * columns, 4), squeeze=False)
    if len(matching) == 0:
        axes[0, 0].text(0.5, 0.5, f"No {CLASS_NAMES[desired_class]} prediction")
        axes[0, 0].axis("off")
        return figure

    for axis, image_index in zip(axes[0], matching):
        image = images[image_index]
        image_batch = tf.convert_to_tensor(image[None, ...], dtype=tf.float32)
        with tf.GradientTape() as tape:
            conv_output, predictions = activation_model(image_batch, training=False)
            class_score = predictions[:, desired_class]
        gradients = tape.gradient(class_score, conv_output)
        pooled_gradients = tf.reduce_mean(gradients, axis=(0, 1, 2))
        heatmap = tf.reduce_sum(conv_output[0] * pooled_gradients, axis=-1)
        heatmap = tf.maximum(heatmap, 0)
        heatmap /= tf.reduce_max(heatmap) + tf.keras.backend.epsilon()
        heatmap = tf.image.resize(
            heatmap[..., None], result.model.input_shape[1:3]
        ).numpy().squeeze()
        axis.imshow(image)
        axis.imshow(heatmap, cmap="jet", alpha=0.45)
        axis.set_title(
            f"{CLASS_NAMES[desired_class]}: {probabilities[image_index, desired_class]:.3f}"
        )
        axis.axis("off")
    figure.tight_layout()
    return figure
