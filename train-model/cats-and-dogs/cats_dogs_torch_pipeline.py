"""PyTorch training and MLflow tracking pipeline for cats versus dogs."""

from __future__ import annotations

import copy
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
from typing import Any, Callable

import matplotlib.pyplot as plt
import mlflow
import mlflow.data
import mlflow.pytorch
import numpy as np
import pandas as pd
import torch
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
from torch import nn
from torch.utils.data import DataLoader
from torchvision import datasets, transforms


CLASS_NAMES = ("Cat", "Dog")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
MODULE_PATH = Path(__file__).resolve()
os.environ.setdefault("MLFLOW_RECORD_ENV_VARS_IN_MODEL_LOGGING", "false")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() == "true"


@dataclass(frozen=True)
class PipelineConfig:
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
    require_gpu: bool = True
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
            os.getenv("TRAIN_REPO_ROOT", "/data/ai/chenzhangyue/code/galatea")
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
                repo_root / "train-model/cats-and-dogs/cats-vs-dogs-classification.ipynb"
            ),
            tracking_uri=os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000"),
            experiment_name=os.getenv(
                "MLFLOW_EXPERIMENT_NAME", "cats-vs-dogs-enterprise"
            ),
            dataset_source_uri=os.getenv("CATS_DOGS_DATASET_SOURCE_URI"),
            dataset_version_override=os.getenv("CATS_DOGS_DATASET_VERSION"),
            run_group_id=run_group_id,
            epochs=int(os.getenv("CATS_DOGS_EPOCHS", "1")),
            min_test_accuracy=float(os.getenv("CATS_DOGS_MIN_TEST_ACCURACY", "0.80")),
            require_gpu=_env_bool("CATS_DOGS_REQUIRE_GPU", True),
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
    training: DataLoader
    validation: DataLoader
    test: DataLoader
    batch_size: int
    augmentation: dict[str, Any]
    input_scaling: str = "torchvision ToTensor: uint8 / 255.0"


@dataclass
class ExperimentResult:
    run_id: str
    model_uri: str | None
    artifact_uri: str
    model: nn.Module
    history: pd.DataFrame
    test_metrics: dict[str, float]
    predictions: pd.DataFrame
    confusion_matrix: np.ndarray
    quality_gate_passed: bool | None


def _command_output(arguments: list[str], cwd: Path) -> str | None:
    result = subprocess.run(
        arguments, cwd=cwd, check=False, capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


def configure_torch_runtime(config: PipelineConfig) -> torch.device:
    """Select CUDA 13-capable PyTorch device and reject silent CPU fallback."""

    if not torch.cuda.is_available():
        if config.require_gpu:
            raise RuntimeError(
                "PyTorch did not register a CUDA GPU. Check that the Jupyter/Ray worker "
                "uses the project environment with torch==2.11.0 (CUDA 13), that the "
                "NVIDIA driver is visible, and that CUDA_VISIBLE_DEVICES is not empty. "
                "Set CATS_DOGS_REQUIRE_GPU=false only for an intentional CPU smoke run."
            )
        return torch.device("cpu")

    if not (torch.version.cuda or "").startswith("13."):
        raise RuntimeError(
            "The active PyTorch build does not use CUDA 13. Install the project's "
            "torch==2.11.0 CUDA 13 wheel and restart the Python/Jupyter kernel."
        )

    device = torch.device("cuda:0")
    torch.cuda.set_device(device)
    torch.manual_seed(config.seed)
    torch.cuda.manual_seed_all(config.seed)
    torch.backends.cudnn.benchmark = False
    return device


def preflight_tracking(config: PipelineConfig) -> TrackingContext:
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
                    "relative_path": source_path.relative_to(config.pet_images_dir).as_posix(),
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
            f"{record['relative_path']}|{record['bytes']}|{record['sha256']}\n".encode()
        )
        split_hasher.update(f"{record['relative_path']}|{record['split']}\n".encode())
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
                class_name: int(count) for class_name, count in class_counts.items()
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
        "rotation_degrees": 20,
        "translation_fraction": 0.2,
    }


def _loader(
    root: Path,
    image_size: tuple[int, int],
    batch_size: int,
    *,
    training: bool,
    augmented: bool,
    seed: int,
) -> DataLoader:
    operations: list[Any] = [transforms.Resize(image_size)]
    if training and augmented:
        operations.extend(
            [
                transforms.RandomHorizontalFlip(),
                transforms.RandomRotation(20),
                transforms.RandomAffine(
                    degrees=0, translate=(0.2, 0.2), fill=0
                ),
            ]
        )
    operations.append(transforms.ToTensor())
    image_dataset = datasets.ImageFolder(str(root), transform=transforms.Compose(operations))
    expected = {"cats": 0, "dogs": 1}
    if image_dataset.class_to_idx != expected:
        raise RuntimeError(
            f"Unexpected class mapping {image_dataset.class_to_idx}; expected {expected}"
        )
    generator = torch.Generator()
    generator.manual_seed(seed)
    return DataLoader(
        image_dataset,
        batch_size=batch_size,
        shuffle=training,
        generator=generator,
        num_workers=0,
        pin_memory=torch.cuda.is_available(),
    )


def create_generators(
    config: PipelineConfig,
    dataset: PreparedDataset,
    *,
    augmented: bool,
) -> DataGenerators:
    policy = augmentation_policy(augmented)
    batch_size = config.augmented_batch_size if augmented else config.baseline_batch_size
    return DataGenerators(
        training=_loader(
            dataset.training_dir, config.image_size, batch_size,
            training=True, augmented=augmented, seed=config.seed,
        ),
        validation=_loader(
            dataset.validation_dir, config.image_size, batch_size,
            training=False, augmented=False, seed=config.seed,
        ),
        test=_loader(
            dataset.test_dir, config.image_size, batch_size,
            training=False, augmented=False, seed=config.seed,
        ),
        batch_size=batch_size,
        augmentation=policy,
    )


class CatsDogsCNN(nn.Module):
    """Small CNN matching the original baseline and augmented variants."""

    def __init__(self, variant: str) -> None:
        super().__init__()
        if variant not in {"baseline", "augmented"}:
            raise ValueError(f"Unsupported model variant: {variant}")
        layers: list[nn.Module] = []
        channels = (3, 32, 64, 64, 128, 128, 256)
        for index in range(len(channels) - 1):
            layers.extend(
                [
                    nn.Conv2d(channels[index], channels[index + 1], 3),
                    nn.ReLU(inplace=False),
                ]
            )
            if index in {1, 3} or (variant == "augmented" and index == 5):
                layers.append(nn.MaxPool2d(2))
        self.features = nn.Sequential(*layers)
        self.last_conv = next(
            layer for layer in reversed(self.features) if isinstance(layer, nn.Conv2d)
        )
        dense_units = 1024 if variant == "baseline" else 256
        self.pool = nn.AdaptiveAvgPool2d((1, 1))
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(256, dense_units),
            nn.ReLU(inplace=False),
            nn.Linear(dense_units, len(CLASS_NAMES)),
        )
        self.variant = variant

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        return self.classifier(self.pool(self.features(inputs)))

    def count_params(self) -> int:
        return sum(parameter.numel() for parameter in self.parameters())


def build_model(config: PipelineConfig, variant: str) -> CatsDogsCNN:
    configure_torch_runtime(config)
    model = CatsDogsCNN(variant)
    model._optimizer_name = "rmsprop"  # type: ignore[attr-defined]
    model._learning_rate = config.learning_rate  # type: ignore[attr-defined]
    return model


def _split_digest(split_frame: pd.DataFrame) -> str:
    payload = split_frame[["relative_path", "label", "bytes", "sha256"]].to_csv(
        index=False
    ).encode()
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
    model: nn.Module,
    loader: DataLoader,
    split_root: Path,
    device: torch.device,
) -> tuple[dict[str, float], dict[str, Any], pd.DataFrame, np.ndarray]:
    model.eval()
    criterion = nn.CrossEntropyLoss()
    labels: list[int] = []
    predictions: list[int] = []
    probabilities: list[np.ndarray] = []
    total_loss = 0.0
    total_count = 0
    with torch.no_grad():
        for images, batch_labels in loader:
            images = images.to(device, non_blocking=True)
            batch_labels = batch_labels.to(device, non_blocking=True)
            logits = model(images)
            loss = criterion(logits, batch_labels)
            probs = torch.softmax(logits, dim=1)
            total_loss += float(loss.item()) * len(batch_labels)
            total_count += len(batch_labels)
            labels.extend(batch_labels.cpu().tolist())
            predictions.extend(probs.argmax(dim=1).cpu().tolist())
            probabilities.extend(probs.cpu().numpy())
    labels_array = np.asarray(labels, dtype="int32")
    predictions_array = np.asarray(predictions, dtype="int32")
    probability_array = np.asarray(probabilities, dtype="float32")
    metrics = {
        "test_loss": total_loss / max(1, total_count),
        "test_accuracy": float((labels_array == predictions_array).mean()),
        "test_precision": float(
            precision_score(labels_array, predictions_array, zero_division=0)
        ),
        "test_recall": float(
            recall_score(labels_array, predictions_array, zero_division=0)
        ),
        "test_f1": float(f1_score(labels_array, predictions_array, zero_division=0)),
        "test_roc_auc": float(roc_auc_score(labels_array, probability_array[:, 1])),
    }
    report = classification_report(
        labels_array,
        predictions_array,
        target_names=CLASS_NAMES,
        output_dict=True,
        zero_division=0,
    )
    filepaths = [path for path, _ in loader.dataset.samples]
    predictions_frame = pd.DataFrame(
        {
            "relative_path": [
                Path(path).relative_to(split_root).as_posix() for path in filepaths
            ],
            "actual_label": labels_array,
            "actual_class": [CLASS_NAMES[label] for label in labels_array],
            "predicted_label": predictions_array,
            "predicted_class": [CLASS_NAMES[label] for label in predictions_array],
            "probability_cat": probability_array[:, 0],
            "probability_dog": probability_array[:, 1],
        }
    )
    return (
        metrics,
        report,
        predictions_frame,
        confusion_matrix(labels_array, predictions_array),
    )


def _environment_report(training_device: torch.device) -> dict[str, Any]:
    gpu_devices = []
    if torch.cuda.is_available():
        for index in range(torch.cuda.device_count()):
            properties = torch.cuda.get_device_properties(index)
            gpu_devices.append(
                {
                    "index": index,
                    "name": properties.name,
                    "capability": f"{properties.major}.{properties.minor}",
                    "memory_bytes": properties.total_memory,
                }
            )
    return {
        "python": platform.python_version(),
        "torch": torch.__version__,
        "torchvision": __import__("torchvision").__version__,
        "cuda_runtime": torch.version.cuda,
        "cudnn": torch.backends.cudnn.version(),
        "mlflow": mlflow.__version__,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "operating_system": platform.platform(),
        "gpu_devices": gpu_devices,
        "training_device": str(training_device),
    }


def plot_training_history(history: pd.DataFrame) -> plt.Figure:
    figure, axes = plt.subplots(1, 2, figsize=(13, 4))
    history.plot(x="epoch", y=["train_accuracy", "val_accuracy"], marker="o", ax=axes[0])
    history.plot(x="epoch", y=["train_loss", "val_loss"], marker="o", ax=axes[1])
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
    threshold = matrix.max() / 2 if matrix.size else 0
    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            axis.text(
                column, row, str(matrix[row, column]), ha="center", va="center",
                color="white" if matrix[row, column] > threshold else "black",
            )
    figure.tight_layout()
    return figure


def _log_training_plots(history: pd.DataFrame, matrix: np.ndarray | None = None) -> None:
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
    model: nn.Module,
    variant: str,
    training_device: torch.device,
) -> dict[str, Any]:
    optimizer_name = str(getattr(model, "_optimizer_name", "rmsprop"))
    return {
        "model.variant": variant,
        "framework.name": "pytorch",
        "framework.version": torch.__version__,
        "model.parameter_count": model.count_params(),
        "model.input_height": config.image_size[0],
        "model.input_width": config.image_size[1],
        "model.output_classes": len(CLASS_NAMES),
        "training.epochs_requested": config.epochs,
        "training.batch_size": generators.batch_size,
        "training.learning_rate": float(getattr(model, "_learning_rate", config.learning_rate)),
        "training.optimizer": optimizer_name,
        "training.loss": "cross_entropy",
        "training.seed": config.seed,
        "training.early_stopping_patience": config.early_stopping_patience,
        "training.input_scaling": generators.input_scaling,
        "resources.training_device": str(training_device),
        "resources.gpu_required": config.require_gpu,
        "data.dataset_version": dataset.dataset_version,
        "data.preprocessing_version": "torchvision-image-v2",
        "data.content_sha256": dataset.content_digest,
        "data.split_sha256": dataset.split_digest,
        "data.train_fraction": config.train_fraction,
        "data.training_images": int((dataset.manifest["split"] == "training").sum()),
        "data.validation_images": int((dataset.manifest["split"] == "validation").sum()),
        "data.test_images": int((dataset.manifest["split"] == "test").sum()),
        "quality.min_test_accuracy": config.min_test_accuracy,
        **{f"augmentation.{name}": value for name, value in generators.augmentation.items()},
    }


def _make_optimizer(model: nn.Module, config: PipelineConfig) -> torch.optim.Optimizer:
    learning_rate = float(getattr(model, "_learning_rate", config.learning_rate))
    name = str(getattr(model, "_optimizer_name", "rmsprop")).lower()
    if name == "adam":
        return torch.optim.Adam(model.parameters(), lr=learning_rate)
    if name == "rmsprop":
        return torch.optim.RMSprop(model.parameters(), lr=learning_rate)
    raise ValueError(f"Unsupported optimizer: {name}")


def _epoch(
    model: nn.Module,
    loader: DataLoader,
    criterion: nn.Module,
    device: torch.device,
    optimizer: torch.optim.Optimizer | None = None,
) -> tuple[float, float]:
    training = optimizer is not None
    model.train(training)
    total_loss = 0.0
    total_correct = 0
    total_count = 0
    for images, labels in loader:
        images = images.to(device, non_blocking=True)
        labels = labels.to(device, non_blocking=True).long()
        if training:
            optimizer.zero_grad(set_to_none=True)
        logits = model(images)
        loss = criterion(logits, labels)
        if training:
            if not torch.isfinite(loss):
                raise FloatingPointError("Non-finite training loss")
            loss.backward()
            optimizer.step()
        total_loss += float(loss.item()) * len(labels)
        total_correct += int((logits.argmax(dim=1) == labels).sum().item())
        total_count += len(labels)
    return total_loss / max(1, total_count), total_correct / max(1, total_count)


def run_tracked_training(
    config: PipelineConfig,
    tracking: TrackingContext,
    dataset: PreparedDataset,
    generators: DataGenerators,
    model: nn.Module,
    *,
    variant: str,
    evaluate_test: bool = True,
    log_model: bool = True,
    selection_metric: str = "val_loss",
    extra_parameters: dict[str, Any] | None = None,
    extra_tags: dict[str, Any] | None = None,
    run_name_suffix: str = "",
    extra_source_paths: list[Path] | None = None,
    progress_callback: Callable[[dict[str, float]], None] | None = None,
) -> ExperimentResult:
    """Train and log one auditable PyTorch Run."""

    if mlflow.active_run() is not None:
        raise RuntimeError("Close the active MLflow Run before starting a model variant")
    if variant not in {"baseline", "augmented", "smoke", "tuning-trial", "tuning-champion"}:
        raise ValueError(f"Unsupported tracked variant: {variant}")
    if selection_metric not in {"val_loss", "val_accuracy"}:
        raise ValueError(f"Unsupported selection metric: {selection_metric}")
    device = configure_torch_runtime(config)
    model = model.to(device)
    optimizer = _make_optimizer(model, config)
    criterion = nn.CrossEntropyLoss()
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
    parameters = _run_parameters(config, dataset, generators, model, variant, device)
    parameters.update(extra_parameters or {})
    run_name = f"{config.run_group_id}-{variant}{run_name_suffix}"
    phase = "run-setup"
    selection_mode = "min" if selection_metric == "val_loss" else "max"

    mlflow.set_tracking_uri(config.tracking_uri)
    with mlflow.start_run(
        experiment_id=tracking.experiment_id,
        run_name=run_name,
        tags=tags,
        description="PyTorch CUDA 13 cats-vs-dogs classifier with content-addressed data lineage.",
        log_system_metrics=True,
    ) as active_run:
        run_id = active_run.info.run_id
        checkpoint_dir = dataset.split_root / "checkpoints" / run_id
        checkpoint_dir.mkdir(parents=True, exist_ok=False)
        checkpoint_path = checkpoint_dir / "best-model.pt"
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
            mlflow.log_dict(_environment_report(device), "environment/runtime.json")
            _log_dataset_inputs(dataset)

            phase = "model-training"
            started_at = time.perf_counter()
            history_rows: list[dict[str, float]] = []
            best_value = float("inf") if selection_mode == "min" else -float("inf")
            best_epoch = 0
            no_improvement = 0
            for epoch_index in range(config.epochs):
                epoch_started = time.perf_counter()
                train_loss, train_accuracy = _epoch(
                    model, generators.training, criterion, device, optimizer
                )
                with torch.no_grad():
                    val_loss, val_accuracy = _epoch(
                        model, generators.validation, criterion, device
                    )
                values = {
                    "epoch": float(epoch_index + 1),
                    "train_loss": train_loss,
                    "train_accuracy": train_accuracy,
                    "val_loss": val_loss,
                    "val_accuracy": val_accuracy,
                    "epoch_duration_seconds": time.perf_counter() - epoch_started,
                    "learning_rate": float(optimizer.param_groups[0]["lr"]),
                }
                history_rows.append(values)
                objective_value = values[selection_metric]
                improved = (
                    objective_value < best_value
                    if selection_mode == "min"
                    else objective_value > best_value
                )
                if improved:
                    best_value = objective_value
                    best_epoch = epoch_index + 1
                    no_improvement = 0
                    torch.save(
                        {
                            "model_state_dict": model.state_dict(),
                            "optimizer_state_dict": optimizer.state_dict(),
                            "epoch": best_epoch,
                            "best_metric": best_value,
                        },
                        checkpoint_path,
                    )
                else:
                    no_improvement += 1
                mlflow.log_metrics(
                    {key: value for key, value in values.items() if key != "epoch"},
                    step=epoch_index,
                )
                if progress_callback is not None:
                    progress_callback(
                        {
                            **values,
                            "epochs_requested": float(config.epochs),
                            "best_epoch": float(best_epoch),
                            "best_metric": float(best_value),
                            "no_improvement": float(no_improvement),
                        }
                    )
                if no_improvement >= max(1, config.early_stopping_patience):
                    break
            training_seconds = time.perf_counter() - started_at
            if not checkpoint_path.is_file():
                raise RuntimeError("Training completed without a best checkpoint")
            checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)
            model.load_state_dict(checkpoint["model_state_dict"])
            history = pd.DataFrame(history_rows)
            history["epoch"] = history["epoch"].astype("int64")
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
                    model, generators.test, dataset.split_root, device
                )
                output_digest = hashlib.sha256(predictions.to_csv(index=False).encode()).hexdigest()
                quality_passed = test_metrics["test_accuracy"] >= config.min_test_accuracy
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
                input_batch, _ = next(
                    iter(generators.test if evaluate_test else generators.validation)
                )
                input_example = input_batch[:4].cpu().numpy().astype("float32")
                model_for_logging = copy.deepcopy(model).to("cpu").eval()
                with torch.no_grad():
                    output_example = model_for_logging(
                        torch.from_numpy(input_example)
                    ).numpy()
                model_info = mlflow.pytorch.log_model(
                    model_for_logging,
                    name="model",
                    signature=infer_signature(input_example, output_example),
                    input_example=input_example,
                    code_paths=[str(MODULE_PATH)],
                    serialization_format="pickle",
                    metadata={
                        "dataset_version": dataset.dataset_version,
                        "class_names": CLASS_NAMES,
                        "input_scaling": generators.input_scaling,
                        "output_semantics": "raw class logits; apply softmax for probabilities",
                        "cuda_runtime": torch.version.cuda,
                    },
                )
                model_uri = model_info.model_uri

            phase = "artifact-verification"
            verification_payload = {
                "run_id": run_id,
                "dataset_version": dataset.dataset_version,
                "artifact_uri": active_run.info.artifact_uri,
            }
            mlflow.log_dict(verification_payload, "verification/artifact-round-trip.json")
            verification_dir = checkpoint_dir / "artifact-download"
            verification_dir.mkdir()
            downloaded_path = mlflow.artifacts.download_artifacts(
                run_id=run_id,
                artifact_path="verification/artifact-round-trip.json",
                dst_path=str(verification_dir),
            )
            with Path(downloaded_path).open(encoding="utf-8") as file_handle:
                if json.load(file_handle) != verification_payload:
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


def compare_run_group(config: PipelineConfig, tracking: TrackingContext) -> pd.DataFrame:
    mlflow.set_tracking_uri(config.tracking_uri)
    runs = mlflow.search_runs(
        experiment_ids=[tracking.experiment_id],
        filter_string=f"tags.run_group_id = '{config.run_group_id}'",
        order_by=["metrics.test_accuracy DESC"],
    )
    columns = [
        "run_id", "tags.variant", "status", "metrics.test_accuracy",
        "metrics.test_f1", "metrics.test_roc_auc", "metrics.best_val_loss",
        "metrics.training_duration_seconds", "tags.quality_gate.passed",
        "tags.artifact.roundtrip_verified", "tags.model.uri",
    ]
    return runs[[column for column in columns if column in runs.columns]]


def plot_dataset_distribution(dataset: PreparedDataset) -> plt.Figure:
    counts = dataset.manifest.groupby("class_name").size().reindex(CLASS_NAMES)
    figure, axis = plt.subplots(figsize=(5, 5))
    axis.pie(counts, labels=counts.index, autopct="%1.1f%%", colors=["#fad25a", "#e4572e"])
    axis.set_title(f"Validated dataset ({int(counts.sum()):,} images)")
    return figure


def _batch_to_numpy(loader: DataLoader, n_images: int) -> tuple[np.ndarray, np.ndarray]:
    images, labels = next(iter(loader))
    count = min(n_images, len(images))
    return images[:count].permute(0, 2, 3, 1).numpy(), labels[:count].numpy().astype("int32")


def plot_image_batch(loader: DataLoader, n_images: int = 9) -> plt.Figure:
    images, labels = _batch_to_numpy(loader, n_images)
    rows = int(np.ceil(len(images) / 3))
    figure = plt.figure(figsize=(12, 4 * rows))
    for index, (image, label) in enumerate(zip(images, labels), start=1):
        axis = figure.add_subplot(rows, 3, index)
        axis.imshow(image)
        axis.set_title(CLASS_NAMES[label])
        axis.axis("off")
    figure.tight_layout()
    return figure


def plot_prediction_examples(
    result: ExperimentResult, loader: DataLoader, n_images: int = 10
) -> plt.Figure:
    images, labels = _batch_to_numpy(loader, n_images)
    device = next(result.model.parameters()).device
    with torch.no_grad():
        logits = result.model(torch.from_numpy(images).permute(0, 3, 1, 2).to(device))
        predictions = logits.argmax(dim=1).cpu().numpy()
    rows = int(np.ceil(len(images) / 3))
    figure = plt.figure(figsize=(12, 4 * rows))
    for index, (image, label) in enumerate(zip(images, labels)):
        axis = figure.add_subplot(rows, 3, index + 1)
        axis.imshow(image)
        predicted = int(predictions[index])
        axis.set_title(
            f"Actual: {CLASS_NAMES[label]} | Predicted: {CLASS_NAMES[predicted]}",
            color="green" if predicted == label else "red",
        )
        axis.axis("off")
    figure.tight_layout()
    return figure


def plot_gradcam_examples(
    result: ExperimentResult,
    loader: DataLoader,
    desired_class: int,
    n_images: int = 5,
) -> plt.Figure:
    if desired_class not in range(len(CLASS_NAMES)):
        raise ValueError(f"Invalid class index: {desired_class}")
    images, labels = _batch_to_numpy(loader, n_images * 4)
    device = next(result.model.parameters()).device
    activations: list[torch.Tensor] = []
    gradients: list[torch.Tensor] = []
    layer = getattr(result.model, "last_conv", None)
    if layer is None:
        raise RuntimeError("The model does not expose a last_conv layer for Grad-CAM")
    # Grad-CAM hooks cannot coexist with in-place activation mutations.
    for module in result.model.modules():
        if isinstance(module, (nn.ReLU, nn.ReLU6)):
            module.inplace = False
    handles: list[Any] = []

    def capture_activation(_, __, output: torch.Tensor) -> None:
        activations.append(output)
        handles.append(output.register_hook(lambda gradient: gradients.append(gradient)))

    try:
        handles.append(layer.register_forward_hook(capture_activation))
        input_tensor = torch.from_numpy(images).permute(0, 3, 1, 2).to(device)
        result.model.eval()
        result.model.zero_grad(set_to_none=True)
        logits = result.model(input_tensor)
        predictions = logits.argmax(dim=1)
        matching = torch.where(predictions == desired_class)[0][:n_images]
        if len(matching) > 0:
            logits[matching, desired_class].sum().backward()
    finally:
        for handle in handles:
            handle.remove()
    columns = max(1, len(matching))
    figure, axes = plt.subplots(1, columns, figsize=(4 * columns, 4), squeeze=False)
    if len(matching) == 0:
        axes[0, 0].text(0.5, 0.5, f"No {CLASS_NAMES[desired_class]} prediction")
        axes[0, 0].axis("off")
        return figure
    activation = activations[0].detach()
    gradient = gradients[0].detach()
    weights = gradient.mean(dim=(2, 3), keepdim=True)
    heatmaps = torch.relu((weights * activation).sum(dim=1, keepdim=True))
    heatmaps = torch.nn.functional.interpolate(
        heatmaps, size=images.shape[1:3], mode="bilinear", align_corners=False
    )
    heatmaps = heatmaps / (heatmaps.amax(dim=(2, 3), keepdim=True) + 1e-8)
    probabilities = torch.softmax(logits.detach(), dim=1).cpu().numpy()
    for axis, image_index in zip(axes[0], matching.cpu().tolist()):
        axis.imshow(images[image_index])
        axis.imshow(heatmaps[image_index, 0].cpu().numpy(), cmap="jet", alpha=0.45)
        axis.set_title(
            f"{CLASS_NAMES[desired_class]}: {probabilities[image_index, desired_class]:.3f}"
        )
        axis.axis("off")
    figure.tight_layout()
    return figure


__all__ = [
    "CLASS_NAMES", "MODULE_PATH", "CatsDogsCNN", "DataGenerators", "ExperimentResult",
    "PipelineConfig", "PreparedDataset", "TrackingContext", "augmentation_policy",
    "build_model", "compare_run_group", "configure_torch_runtime", "create_generators",
    "plot_confusion_matrix", "plot_dataset_distribution", "plot_gradcam_examples",
    "plot_image_batch", "plot_prediction_examples", "plot_training_history",
    "preflight_tracking", "prepare_dataset", "run_tracked_training",
]
