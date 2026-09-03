"""手写数字识别工作负载的类型化 YAML 配置。"""

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
SUPPORTED_MODELS = {"digit_cnn"}


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
        return self.data.image_height, self.data.image_width

    def as_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))

    @property
    def config_digest(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
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
        raise ValueError("循环配置继承")
    with resolved.open(encoding="utf-8") as file_handle:
        payload = yaml.safe_load(file_handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"配置必须是 YAML 映射: {resolved}")
    parent_name = payload.pop("extends", None)
    if parent_name is None:
        return payload
    parent_path = (resolved.parent / str(parent_name)).resolve()
    if parent_path.parent != resolved.parent:
        raise ValueError("配置继承必须位于 configs 目录内")
    return _deep_merge(_read_config(parent_path, (*stack, resolved)), payload)


def _set_override(config: dict[str, Any], expression: str) -> None:
    if "=" not in expression:
        raise ValueError(f"覆盖必须使用 dotted.path=value: {expression}")
    dotted_key, raw_value = expression.split("=", maxsplit=1)
    keys = dotted_key.split(".")
    target: Any = config
    for key in keys[:-1]:
        if not isinstance(target, dict) or key not in target:
            raise ValueError(f"覆盖路径不存在: {dotted_key}")
        target = target[key]
    if not isinstance(target, dict) or keys[-1] not in target:
        raise ValueError(f"覆盖键不存在: {dotted_key}")
    target[keys[-1]] = yaml.safe_load(raw_value)


def _mapping(config: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = config.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"缺少或无效的配置段: {key}")
    return value


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
    overrides = {
        "MLFLOW_TRACKING_URI": ("mlflow", "tracking_uri"),
        "MLFLOW_EXPERIMENT_NAME": ("mlflow", "experiment_name"),
        "HANDWRITTEN_DIGITS_DATA_DIR": ("data", "root"),
        "HANDWRITTEN_DIGITS_DATASET_SOURCE_URI": ("data", "source_uri"),
        "RAY_ADDRESS": ("ray", "address"),
    }
    for environment_name, (section, key) in overrides.items():
        value = os.getenv(environment_name)
        if value is not None:
            config[section][key] = value


def load_config(path: Path, overrides: tuple[str, ...] = ()) -> ProjectConfig:
    source_path = path.resolve()
    raw = _read_config(source_path)
    _apply_environment(raw)
    for expression in overrides:
        _set_override(raw, expression)
    expected_sections = {"project_name", "run", "data", "model", "training", "ray", "mlflow", "evaluation"}
    if set(raw) != expected_sections:
        raise ValueError(f"顶层配置段必须为 {sorted(expected_sections)}")
    project_root = source_path.parent.parent.resolve()
    repo_root = project_root.parents[1]
    run = _mapping(raw, "run")
    data = _mapping(raw, "data")
    model = _mapping(raw, "model")
    training = _mapping(raw, "training")
    ray = _mapping(raw, "ray")
    mlflow = _mapping(raw, "mlflow")
    evaluation = _mapping(raw, "evaluation")
    config = ProjectConfig(
        project_name=str(raw["project_name"]),
        project_root=project_root,
        source_config_path=source_path,
        run=RunSettings(str(run["role"]), str(run["name_prefix"]), int(run["seed"]), bool(run["log_model"])),
        data=DatasetSettings(
            _path(data["root"], repo_root, project_root),
            _path(data["cache_dir"], repo_root, project_root),
            None if data["source_uri"] is None else str(data["source_uri"]),
            None if data["expected_images_per_class"] is None else int(data["expected_images_per_class"]),
            None if data["expected_valid_images"] is None else int(data["expected_valid_images"]),
            float(data["train_fraction"]), float(data["validation_fraction"]), float(data["test_fraction"]),
            int(data["image_height"]), int(data["image_width"]), str(data["preprocessing_version"]),
        ),
        model=ModelSettings(str(model["family"]), int(model["dense_units"]), float(model["dropout"]), bool(model["augmentation"]), None if model["pretrained_weights"] is None else str(model["pretrained_weights"])),
        training=TrainingSettings(int(training["epochs"]), int(training["per_worker_batch_size"]), str(training["mixed_precision"]).lower(), float(training["learning_rate"]), str(training["optimizer"]).lower(), int(training["early_stopping_patience"]), str(training["objective_metric"]), str(training["objective_mode"])),
        ray=RaySettings(
            None if ray["address"] in (None, "", "local") else str(ray["address"]), int(ray["num_workers"]), bool(ray["use_gpu"]), float(ray["cpus_per_worker"]), int(ray["data_num_blocks"]), int(ray["data_decode_workers"]), int(ray["data_decode_batch_size"]), int(ray["data_prefetch_batches"]), bool(ray["data_cache_decoded"]), int(float(ray["memory_per_worker_gb"]) * 1024**3), str(ray["placement_strategy"]), int(ray["max_failures"]), _storage_path(ray["storage_path"], repo_root, project_root), bool(ray["record_task_timeline"]), float(ray["evaluation_cpus"]), float(ray["evaluation_gpus"]), int(float(ray["evaluation_memory_gb"]) * 1024**3),
        ),
        mlflow=MlflowSettings(str(mlflow["tracking_uri"]), str(mlflow["experiment_name"]), bool(mlflow["require_remote_artifacts"])),
        evaluation=EvaluationSettings(bool(evaluation["evaluate_test"]), float(evaluation["minimum_test_accuracy"])),
    )
    _validate(config)
    return config


def _validate(config: ProjectConfig) -> None:
    if config.project_name != "ray-handwritten-digits":
        raise ValueError("project_name 必须为 ray-handwritten-digits")
    if config.run.role not in SUPPORTED_ROLES:
        raise ValueError("run.role 必须是 smoke、trial 或 champion")
    if config.model.family not in SUPPORTED_MODELS or config.model.pretrained_weights is not None:
        raise ValueError("模型必须是无预训练权重的 digit_cnn")
    if config.data.preprocessing_version != "kaggle-digits-grayscale-inverted-spatial-v2":
        raise ValueError("preprocessing_version 必须声明反相与空间特征版本")
    if config.model.dense_units < 1 or not 0 <= config.model.dropout < 1:
        raise ValueError("模型规模或 dropout 无效")
    fractions = (config.data.train_fraction, config.data.validation_fraction, config.data.test_fraction)
    if any(value <= 0 for value in fractions) or abs(sum(fractions) - 1) > 1e-9:
        raise ValueError("数据切分比例必须为正数且总和为 1")
    if config.data.image_height < 8 or config.data.image_width < 8:
        raise ValueError("手写数字图像尺寸不能小于 8")
    if config.training.epochs < 1 or config.training.per_worker_batch_size < 1:
        raise ValueError("epochs 和 batch size 必须为正数")
    if config.training.mixed_precision not in {"none", "bf16"}:
        raise ValueError("mixed_precision 必须是 none 或 bf16")
    if config.training.optimizer not in {"adam", "rmsprop"} or config.training.learning_rate <= 0:
        raise ValueError("优化器或学习率无效")
    if config.training.objective_metric not in SUPPORTED_OBJECTIVES or SUPPORTED_OBJECTIVES[config.training.objective_metric] != config.training.objective_mode:
        raise ValueError("objective_metric 与 objective_mode 不匹配")
    if config.ray.num_workers < 1 or config.ray.data_num_blocks < config.ray.data_decode_workers:
        raise ValueError("Ray worker 与数据并行度配置无效")
    if config.ray.max_failures < 0 or config.ray.evaluation_cpus <= 0 or config.ray.evaluation_memory_bytes <= 0:
        raise ValueError("Ray 资源配置无效")
    if not 0 <= config.ray.evaluation_gpus <= 1:
        raise ValueError("evaluation_gpus 必须在 0 到 1 之间")
    if config.evaluation.evaluate_test and config.run.role != "champion":
        raise ValueError("只有 champion 可以读取测试集")
    if config.run.log_model and config.run.role != "champion":
        raise ValueError("只有 champion 可以记录模型")
    if not 0 <= config.evaluation.minimum_test_accuracy <= 1:
        raise ValueError("minimum_test_accuracy 必须在 0 到 1 之间")
