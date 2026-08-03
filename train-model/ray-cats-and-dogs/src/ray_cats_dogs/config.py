"""Typed YAML configuration for the Ray cats-and-dogs workload."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


SUPPORTED_ROLES = {"smoke", "trial", "champion"}
SUPPORTED_OBJECTIVES = {"val_accuracy": "max", "val_loss": "min"}
SUPPORTED_MODELS = {"custom_cnn", "mobilenet_v2"}


@dataclass(frozen=True)
class RunSettings:
    role: str
    name_prefix: str
    seed: int
    log_model: bool


@dataclass(frozen=True)
class DatasetSettings:
    root: Path
    cache_dir: Path
    source_uri: str | None
    expected_images_per_class: int | None
    expected_valid_images: int | None
    train_fraction: float
    validation_fraction: float
    test_fraction: float
    image_height: int
    image_width: int
    preprocessing_version: str


@dataclass(frozen=True)
class ModelSettings:
    family: str
    dense_units: int
    dropout: float
    augmentation: bool
    pretrained_weights: str | None


@dataclass(frozen=True)
class TrainingSettings:
    epochs: int
    per_worker_batch_size: int
    mixed_precision: str
    learning_rate: float
    optimizer: str
    early_stopping_patience: int
    objective_metric: str
    objective_mode: str


@dataclass(frozen=True)
class RaySettings:
    address: str | None
    num_workers: int
    use_gpu: bool
    cpus_per_worker: float
    data_num_blocks: int
    data_decode_workers: int
    data_decode_batch_size: int
    data_prefetch_batches: int
    data_cache_decoded: bool
    memory_per_worker_bytes: int
    placement_strategy: str
    max_failures: int
    storage_path: str
    record_task_timeline: bool
    evaluation_cpus: float
    evaluation_gpus: float
    evaluation_memory_bytes: int


@dataclass(frozen=True)
class MlflowSettings:
    tracking_uri: str
    experiment_name: str
    require_remote_artifacts: bool


@dataclass(frozen=True)
class EvaluationSettings:
    evaluate_test: bool
    minimum_test_accuracy: float


@dataclass(frozen=True)
class ProjectConfig:
    project_name: str
    project_root: Path
    source_config_path: Path
    run: RunSettings
    data: DatasetSettings
    model: ModelSettings
    training: TrainingSettings
    ray: RaySettings
    mlflow: MlflowSettings
    evaluation: EvaluationSettings

    @property
    def image_size(self) -> tuple[int, int]:
        return (self.data.image_height, self.data.image_width)

    def as_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))

    @property
    def config_digest(self) -> str:
        identity = {
            "project_name": self.project_name,
            "role": self.run.role,
            "seed": self.run.seed,
            "data": {
                "source_uri": self.data.source_uri,
                "train_fraction": self.data.train_fraction,
                "validation_fraction": self.data.validation_fraction,
                "test_fraction": self.data.test_fraction,
                "image_height": self.data.image_height,
                "image_width": self.data.image_width,
                "preprocessing_version": self.data.preprocessing_version,
            },
            "model": _json_ready(asdict(self.model)),
            "training": _json_ready(asdict(self.training)),
            "ray": {
                "num_workers": self.ray.num_workers,
                "use_gpu": self.ray.use_gpu,
                "cpus_per_worker": self.ray.cpus_per_worker,
                "data_num_blocks": self.ray.data_num_blocks,
                "data_decode_workers": self.ray.data_decode_workers,
                "data_decode_batch_size": self.ray.data_decode_batch_size,
                "data_prefetch_batches": self.ray.data_prefetch_batches,
                "data_cache_decoded": self.ray.data_cache_decoded,
            },
            "evaluation": _json_ready(asdict(self.evaluation)),
        }
        payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_config(path: Path, stack: tuple[Path, ...] = ()) -> dict[str, Any]:
    resolved = path.resolve()
    if resolved in stack:
        chain = " -> ".join(str(item) for item in (*stack, resolved))
        raise ValueError(f"Cyclic config inheritance: {chain}")
    with resolved.open(encoding="utf-8") as file_handle:
        payload = yaml.safe_load(file_handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Config must contain a YAML mapping: {resolved}")
    parent_name = payload.pop("extends", None)
    if parent_name is None:
        return payload
    parent_path = (resolved.parent / str(parent_name)).resolve()
    if parent_path.parent != resolved.parent:
        raise ValueError("Config inheritance must stay inside the configs directory")
    return _deep_merge(_read_config(parent_path, (*stack, resolved)), payload)


def _set_override(config: dict[str, Any], expression: str) -> None:
    if "=" not in expression:
        raise ValueError(f"Override must use dotted.path=value: {expression}")
    dotted_key, raw_value = expression.split("=", maxsplit=1)
    keys = dotted_key.split(".")
    if any(not key for key in keys):
        raise ValueError(f"Invalid override path: {dotted_key}")
    target = config
    for key in keys[:-1]:
        value = target.get(key)
        if not isinstance(value, dict):
            raise ValueError(f"Override path does not exist: {dotted_key}")
        target = value
    if keys[-1] not in target:
        raise ValueError(f"Override key does not exist: {dotted_key}")
    target[keys[-1]] = yaml.safe_load(raw_value)


def _mapping(config: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = config.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"Missing or invalid '{key}' config section")
    return value


def _check_keys(section: str, values: Mapping[str, Any], expected: set[str]) -> None:
    unknown = set(values) - expected
    missing = expected - set(values)
    if unknown:
        raise ValueError(f"Unknown {section} keys: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"Missing {section} keys: {', '.join(sorted(missing))}")


def _optional_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    if parsed < 1:
        raise ValueError(f"{name} must be positive or null")
    return parsed


def _path(value: Any, repo_root: Path, project_root: Path) -> Path:
    rendered = str(value).replace("${REPO_ROOT}", str(repo_root)).replace(
        "${PROJECT_ROOT}", str(project_root)
    )
    return Path(os.path.expandvars(os.path.expanduser(rendered))).resolve()


def _storage_path(value: Any, repo_root: Path, project_root: Path) -> str:
    rendered = str(value).replace("${REPO_ROOT}", str(repo_root)).replace(
        "${PROJECT_ROOT}", str(project_root)
    )
    rendered = os.path.expandvars(os.path.expanduser(rendered))
    return rendered if "://" in rendered else str(Path(rendered).resolve())


def _apply_environment(config: dict[str, Any]) -> None:
    environment_overrides = {
        "MLFLOW_TRACKING_URI": ("mlflow", "tracking_uri"),
        "MLFLOW_EXPERIMENT_NAME": ("mlflow", "experiment_name"),
        "CATS_DOGS_DATA_DIR": ("data", "root"),
        "CATS_DOGS_DATASET_SOURCE_URI": ("data", "source_uri"),
        "RAY_ADDRESS": ("ray", "address"),
    }
    for environment_name, (section, key) in environment_overrides.items():
        value = os.getenv(environment_name)
        if value is not None:
            config[section][key] = value


def load_config(path: Path, overrides: tuple[str, ...] = ()) -> ProjectConfig:
    """Load, compose, override, type, and validate one workload config."""

    source_path = path.resolve()
    raw = _read_config(source_path)
    _apply_environment(raw)
    for expression in overrides:
        _set_override(raw, expression)

    expected_sections = {
        "project_name",
        "run",
        "data",
        "model",
        "training",
        "ray",
        "mlflow",
        "evaluation",
    }
    unknown_sections = set(raw) - expected_sections
    missing_sections = expected_sections - set(raw)
    if unknown_sections or missing_sections:
        details = []
        if unknown_sections:
            details.append(f"unknown={sorted(unknown_sections)}")
        if missing_sections:
            details.append(f"missing={sorted(missing_sections)}")
        raise ValueError("Invalid top-level config sections: " + ", ".join(details))

    project_root = source_path.parent.parent.resolve()
    repo_root = project_root.parents[1]
    run_values = _mapping(raw, "run")
    data_values = _mapping(raw, "data")
    model_values = _mapping(raw, "model")
    training_values = _mapping(raw, "training")
    ray_values = _mapping(raw, "ray")
    mlflow_values = _mapping(raw, "mlflow")
    evaluation_values = _mapping(raw, "evaluation")

    _check_keys("run", run_values, {"role", "name_prefix", "seed", "log_model"})
    _check_keys(
        "data",
        data_values,
        {
            "root",
            "cache_dir",
            "source_uri",
            "expected_images_per_class",
            "expected_valid_images",
            "train_fraction",
            "validation_fraction",
            "test_fraction",
            "image_height",
            "image_width",
            "preprocessing_version",
        },
    )
    _check_keys(
        "model",
        model_values,
        {"family", "dense_units", "dropout", "augmentation", "pretrained_weights"},
    )
    _check_keys(
        "training",
        training_values,
        {
            "epochs",
            "per_worker_batch_size",
            "mixed_precision",
            "learning_rate",
            "optimizer",
            "early_stopping_patience",
            "objective_metric",
            "objective_mode",
        },
    )
    _check_keys(
        "ray",
        ray_values,
        {
            "address",
            "num_workers",
            "use_gpu",
            "cpus_per_worker",
            "data_num_blocks",
            "data_decode_workers",
            "data_decode_batch_size",
            "data_prefetch_batches",
            "data_cache_decoded",
            "memory_per_worker_gb",
            "placement_strategy",
            "max_failures",
            "storage_path",
            "record_task_timeline",
            "evaluation_cpus",
            "evaluation_gpus",
            "evaluation_memory_gb",
        },
    )
    _check_keys(
        "mlflow",
        mlflow_values,
        {"tracking_uri", "experiment_name", "require_remote_artifacts"},
    )
    _check_keys(
        "evaluation",
        evaluation_values,
        {"evaluate_test", "minimum_test_accuracy"},
    )

    config = ProjectConfig(
        project_name=str(raw["project_name"]),
        project_root=project_root,
        source_config_path=source_path,
        run=RunSettings(
            role=str(run_values["role"]),
            name_prefix=str(run_values["name_prefix"]),
            seed=int(run_values["seed"]),
            log_model=bool(run_values["log_model"]),
        ),
        data=DatasetSettings(
            root=_path(data_values["root"], repo_root, project_root),
            cache_dir=_path(data_values["cache_dir"], repo_root, project_root),
            source_uri=(
                str(data_values["source_uri"])
                if data_values["source_uri"] is not None
                else None
            ),
            expected_images_per_class=_optional_int(
                data_values["expected_images_per_class"],
                "expected_images_per_class",
            ),
            expected_valid_images=_optional_int(
                data_values["expected_valid_images"], "expected_valid_images"
            ),
            train_fraction=float(data_values["train_fraction"]),
            validation_fraction=float(data_values["validation_fraction"]),
            test_fraction=float(data_values["test_fraction"]),
            image_height=int(data_values["image_height"]),
            image_width=int(data_values["image_width"]),
            preprocessing_version=str(data_values["preprocessing_version"]),
        ),
        model=ModelSettings(
            family=str(model_values["family"]),
            dense_units=int(model_values["dense_units"]),
            dropout=float(model_values["dropout"]),
            augmentation=bool(model_values["augmentation"]),
            pretrained_weights=(
                str(model_values["pretrained_weights"])
                if model_values["pretrained_weights"] is not None
                else None
            ),
        ),
        training=TrainingSettings(
            epochs=int(training_values["epochs"]),
            per_worker_batch_size=int(training_values["per_worker_batch_size"]),
            mixed_precision=str(training_values["mixed_precision"]).lower(),
            learning_rate=float(training_values["learning_rate"]),
            optimizer=str(training_values["optimizer"]).lower(),
            early_stopping_patience=int(training_values["early_stopping_patience"]),
            objective_metric=str(training_values["objective_metric"]),
            objective_mode=str(training_values["objective_mode"]),
        ),
        ray=RaySettings(
            address=(
                None
                if ray_values["address"] in (None, "", "local")
                else str(ray_values["address"])
            ),
            num_workers=int(ray_values["num_workers"]),
            use_gpu=bool(ray_values["use_gpu"]),
            cpus_per_worker=float(ray_values["cpus_per_worker"]),
            data_num_blocks=int(ray_values["data_num_blocks"]),
            data_decode_workers=int(ray_values["data_decode_workers"]),
            data_decode_batch_size=int(ray_values["data_decode_batch_size"]),
            data_prefetch_batches=int(ray_values["data_prefetch_batches"]),
            data_cache_decoded=bool(ray_values["data_cache_decoded"]),
            memory_per_worker_bytes=int(
                float(ray_values["memory_per_worker_gb"]) * 1024**3
            ),
            placement_strategy=str(ray_values["placement_strategy"]),
            max_failures=int(ray_values["max_failures"]),
            storage_path=_storage_path(
                ray_values["storage_path"], repo_root, project_root
            ),
            record_task_timeline=bool(ray_values["record_task_timeline"]),
            evaluation_cpus=float(ray_values["evaluation_cpus"]),
            evaluation_gpus=float(ray_values["evaluation_gpus"]),
            evaluation_memory_bytes=int(
                float(ray_values["evaluation_memory_gb"]) * 1024**3
            ),
        ),
        mlflow=MlflowSettings(
            tracking_uri=str(mlflow_values["tracking_uri"]),
            experiment_name=str(mlflow_values["experiment_name"]),
            require_remote_artifacts=bool(mlflow_values["require_remote_artifacts"]),
        ),
        evaluation=EvaluationSettings(
            evaluate_test=bool(evaluation_values["evaluate_test"]),
            minimum_test_accuracy=float(
                evaluation_values["minimum_test_accuracy"]
            ),
        ),
    )
    _validate(config)
    return config


def _validate(config: ProjectConfig) -> None:
    if not config.project_name:
        raise ValueError("project_name cannot be empty")
    if config.run.role not in SUPPORTED_ROLES:
        raise ValueError(f"run.role must be one of {sorted(SUPPORTED_ROLES)}")
    if config.run.seed < 0:
        raise ValueError("run.seed must be non-negative")
    if config.model.family not in SUPPORTED_MODELS:
        raise ValueError(f"model.family must be one of {sorted(SUPPORTED_MODELS)}")
    if config.model.family == "custom_cnn" and config.model.pretrained_weights:
        raise ValueError("custom_cnn does not accept pretrained_weights")
    if config.model.pretrained_weights not in {None, "imagenet"}:
        raise ValueError("pretrained_weights must be null or 'imagenet'")
    if config.model.dense_units < 1 or not 0 <= config.model.dropout < 1:
        raise ValueError("dense_units must be positive and dropout must be in [0, 1)")

    fractions = (
        config.data.train_fraction,
        config.data.validation_fraction,
        config.data.test_fraction,
    )
    if any(fraction <= 0 for fraction in fractions) or abs(sum(fractions) - 1) > 1e-9:
        raise ValueError("data split fractions must be positive and sum to 1")
    if config.data.image_height < 32 or config.data.image_width < 32:
        raise ValueError("image dimensions must be at least 32 pixels")
    if not config.data.preprocessing_version:
        raise ValueError("data.preprocessing_version cannot be empty")

    if config.training.epochs < 1 or config.training.per_worker_batch_size < 1:
        raise ValueError("epochs and per_worker_batch_size must be positive")
    if config.training.mixed_precision not in {"none", "bf16"}:
        raise ValueError("training.mixed_precision must be none or bf16")
    if config.training.learning_rate <= 0:
        raise ValueError("training.learning_rate must be positive")
    if config.training.optimizer not in {"adam", "rmsprop"}:
        raise ValueError("training.optimizer must be adam or rmsprop")
    expected_mode = SUPPORTED_OBJECTIVES.get(config.training.objective_metric)
    if expected_mode is None:
        raise ValueError("objective_metric must be val_accuracy or val_loss")
    if config.training.objective_mode != expected_mode:
        raise ValueError(
            f"{config.training.objective_metric} requires objective_mode={expected_mode}"
        )
    if config.training.early_stopping_patience < 0:
        raise ValueError("early_stopping_patience must be non-negative")

    if config.ray.num_workers < 1 or config.ray.cpus_per_worker <= 0:
        raise ValueError("Ray requires at least one worker and positive worker CPUs")
    if config.ray.data_num_blocks < 1:
        raise ValueError("ray.data_num_blocks must be positive")
    if config.ray.data_decode_workers < 1:
        raise ValueError("ray.data_decode_workers must be positive")
    if config.ray.data_num_blocks < config.ray.data_decode_workers:
        raise ValueError("ray.data_num_blocks must be at least data_decode_workers")
    if config.ray.data_decode_batch_size < 1:
        raise ValueError("ray.data_decode_batch_size must be positive")
    if config.ray.data_prefetch_batches < 0:
        raise ValueError("ray.data_prefetch_batches cannot be negative")
    if config.ray.memory_per_worker_bytes <= 0:
        raise ValueError("Ray worker memory must be positive")
    if config.ray.placement_strategy not in {"PACK", "SPREAD", "STRICT_PACK", "STRICT_SPREAD"}:
        raise ValueError("Unsupported Ray placement_strategy")
    if config.ray.max_failures < 0:
        raise ValueError("ray.max_failures must be non-negative")
    if config.ray.evaluation_cpus <= 0 or config.ray.evaluation_memory_bytes <= 0:
        raise ValueError("Ray evaluation CPU and memory requests must be positive")
    if not 0 <= config.ray.evaluation_gpus <= 1:
        raise ValueError("ray.evaluation_gpus must be between 0 and 1")
    if config.ray.num_workers > 1 and config.ray.storage_path.startswith("/tmp/"):
        raise ValueError("multi-worker Ray storage_path cannot use /tmp")

    if not config.mlflow.tracking_uri or not config.mlflow.experiment_name:
        raise ValueError("MLflow tracking_uri and experiment_name are required")
    if not 0 <= config.evaluation.minimum_test_accuracy <= 1:
        raise ValueError("minimum_test_accuracy must be between 0 and 1")
    if config.evaluation.evaluate_test and config.run.role != "champion":
        raise ValueError("Only a champion config may evaluate the test holdout")
    if config.run.log_model and config.run.role != "champion":
        raise ValueError("Only a champion config may log an MLflow model")
