"""House Prices 工作负载的严格类型化 YAML 配置。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


SUPPORTED_ROLES = {"smoke", "trial", "champion"}
SUPPORTED_FAMILIES = {
    "elastic_net",
    "lasso",
    "ridge",
    "kernel_ridge",
    "svr",
    "gradient_boosting",
    "xgboost",
    "lightgbm",
    "catboost",
    "catboost_native",
}


@dataclass(frozen=True)
class RunSettings:
    role: str
    name_prefix: str
    seed: int
    log_model: bool


@dataclass(frozen=True)
class DataSettings:
    train_csv: Path
    test_csv: Path
    sample_submission_csv: Path
    cache_dir: Path
    output_dir: Path
    source_uri: str
    expected_train_rows: int
    expected_test_rows: int
    id_column: str
    target_column: str
    preprocessing_version: str
    schema_version: str
    holdout_fraction: float


@dataclass(frozen=True)
class ValidationSettings:
    folds: int
    tuning_folds: int
    stratification_bins: int


@dataclass(frozen=True)
class ModelSettings:
    enabled: tuple[str, ...]
    parameters: dict[str, dict[str, Any]]
    blend_l2: float
    selected_parameters_path: Path | None


@dataclass(frozen=True)
class TuningSettings:
    enabled: bool
    trials_per_family: int
    timeout_seconds_per_family: int


@dataclass(frozen=True)
class TrainingSettings:
    objective_metric: str
    objective_mode: str
    n_jobs: int
    remove_submission_outliers: bool


@dataclass(frozen=True)
class RaySettings:
    address: str | None
    cpus: float
    memory_bytes: int
    storage_path: Path


@dataclass(frozen=True)
class MlflowSettings:
    tracking_uri: str
    experiment_name: str
    require_remote_artifacts: bool


@dataclass(frozen=True)
class EvaluationSettings:
    evaluate_holdout: bool
    maximum_holdout_rmsle: float


@dataclass(frozen=True)
class SubmissionSettings:
    generate: bool
    filename: str


@dataclass(frozen=True)
class ProjectConfig:
    project_name: str
    project_root: Path
    source_config_path: Path
    run: RunSettings
    data: DataSettings
    validation: ValidationSettings
    models: ModelSettings
    tuning: TuningSettings
    training: TrainingSettings
    ray: RaySettings
    mlflow: MlflowSettings
    evaluation: EvaluationSettings
    submission: SubmissionSettings

    def as_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))

    @property
    def config_digest(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
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
        raise ValueError("检测到循环配置继承")
    payload = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"配置必须是 YAML 映射: {resolved}")
    parent_name = payload.pop("extends", None)
    if parent_name is None:
        return payload
    parent_path = (resolved.parent / str(parent_name)).resolve()
    if parent_path.parent != resolved.parent:
        raise ValueError("配置继承只能引用同一个 configs 目录中的文件")
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


def _path(value: Any, repository_root: Path, project_root: Path) -> Path:
    rendered = str(value).replace("${REPO_ROOT}", str(repository_root)).replace(
        "${PROJECT_ROOT}", str(project_root)
    )
    return Path(os.path.expandvars(os.path.expanduser(rendered))).resolve()


def _apply_environment(config: dict[str, Any]) -> None:
    bindings = {
        "MLFLOW_TRACKING_URI": ("mlflow", "tracking_uri"),
        "MLFLOW_EXPERIMENT_NAME": ("mlflow", "experiment_name"),
        "HOUSE_PRICES_TRAIN_CSV": ("data", "train_csv"),
        "HOUSE_PRICES_TEST_CSV": ("data", "test_csv"),
        "RAY_ADDRESS": ("ray", "address"),
    }
    for environment_name, (section, key) in bindings.items():
        value = os.getenv(environment_name)
        if value is not None:
            config[section][key] = value


def load_config(path: Path, overrides: tuple[str, ...] = ()) -> ProjectConfig:
    source_path = path.resolve()
    raw = _read_config(source_path)
    _apply_environment(raw)
    for expression in overrides:
        _set_override(raw, expression)
    expected_sections = {
        "project_name", "run", "data", "validation", "models", "tuning",
        "training", "ray", "mlflow", "evaluation", "submission",
    }
    if set(raw) != expected_sections:
        raise ValueError(f"顶层配置段必须为 {sorted(expected_sections)}")
    project_root = source_path.parent.parent.resolve()
    repository_root = project_root.parents[1]
    run = _mapping(raw, "run")
    data = _mapping(raw, "data")
    validation = _mapping(raw, "validation")
    models = _mapping(raw, "models")
    tuning = _mapping(raw, "tuning")
    training = _mapping(raw, "training")
    ray = _mapping(raw, "ray")
    mlflow = _mapping(raw, "mlflow")
    evaluation = _mapping(raw, "evaluation")
    submission = _mapping(raw, "submission")
    parameters = models.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ValueError("models.parameters 必须是映射")
    config = ProjectConfig(
        project_name=str(raw["project_name"]),
        project_root=project_root,
        source_config_path=source_path,
        run=RunSettings(
            role=str(run["role"]),
            name_prefix=str(run["name_prefix"]),
            seed=int(run["seed"]),
            log_model=bool(run["log_model"]),
        ),
        data=DataSettings(
            train_csv=_path(data["train_csv"], repository_root, project_root),
            test_csv=_path(data["test_csv"], repository_root, project_root),
            sample_submission_csv=_path(data["sample_submission_csv"], repository_root, project_root),
            cache_dir=_path(data["cache_dir"], repository_root, project_root),
            output_dir=_path(data["output_dir"], repository_root, project_root),
            source_uri=str(data["source_uri"]),
            expected_train_rows=int(data["expected_train_rows"]),
            expected_test_rows=int(data["expected_test_rows"]),
            id_column=str(data["id_column"]),
            target_column=str(data["target_column"]),
            preprocessing_version=str(data["preprocessing_version"]),
            schema_version=str(data["schema_version"]),
            holdout_fraction=float(data["holdout_fraction"]),
        ),
        validation=ValidationSettings(
            folds=int(validation["folds"]),
            tuning_folds=int(validation["tuning_folds"]),
            stratification_bins=int(validation["stratification_bins"]),
        ),
        models=ModelSettings(
            enabled=tuple(str(item) for item in models["enabled"]),
            parameters={str(name): dict(value) for name, value in parameters.items()},
            blend_l2=float(models["blend_l2"]),
            selected_parameters_path=(
                None
                if models.get("selected_parameters_path") in (None, "")
                else _path(models["selected_parameters_path"], repository_root, project_root)
            ),
        ),
        tuning=TuningSettings(
            enabled=bool(tuning["enabled"]),
            trials_per_family=int(tuning["trials_per_family"]),
            timeout_seconds_per_family=int(tuning["timeout_seconds_per_family"]),
        ),
        training=TrainingSettings(
            objective_metric=str(training["objective_metric"]),
            objective_mode=str(training["objective_mode"]),
            n_jobs=int(training["n_jobs"]),
            remove_submission_outliers=bool(training["remove_submission_outliers"]),
        ),
        ray=RaySettings(
            address=None if ray["address"] in (None, "", "local") else str(ray["address"]),
            cpus=float(ray["cpus"]),
            memory_bytes=int(float(ray["memory_gb"]) * 1024**3),
            storage_path=_path(ray["storage_path"], repository_root, project_root),
        ),
        mlflow=MlflowSettings(
            tracking_uri=str(mlflow["tracking_uri"]),
            experiment_name=str(mlflow["experiment_name"]),
            require_remote_artifacts=bool(mlflow["require_remote_artifacts"]),
        ),
        evaluation=EvaluationSettings(
            evaluate_holdout=bool(evaluation["evaluate_holdout"]),
            maximum_holdout_rmsle=float(evaluation["maximum_holdout_rmsle"]),
        ),
        submission=SubmissionSettings(
            generate=bool(submission["generate"]),
            filename=str(submission["filename"]),
        ),
    )
    _validate(config)
    return config


def _validate(config: ProjectConfig) -> None:
    if config.project_name != "ray-kaggle-house-prices":
        raise ValueError("project_name 必须为 ray-kaggle-house-prices")
    if config.run.role not in SUPPORTED_ROLES:
        raise ValueError("run.role 必须是 smoke、trial 或 champion")
    if not config.models.enabled or len(set(config.models.enabled)) != len(config.models.enabled):
        raise ValueError("models.enabled 必须是非空且无重复的模型列表")
    unknown = set(config.models.enabled) - SUPPORTED_FAMILIES
    if unknown:
        raise ValueError(f"不支持的模型族: {sorted(unknown)}")
    if not set(config.models.enabled).issubset(config.models.parameters):
        raise ValueError("models.parameters 必须覆盖 models.enabled")
    if config.training.objective_metric != "val_rmsle" or config.training.objective_mode != "min":
        raise ValueError("House Prices 目标必须是 val_rmsle/min")
    if not 0 <= config.data.holdout_fraction < 0.5:
        raise ValueError("data.holdout_fraction 必须在 0（仅 trial）和 0.5 之间")
    if config.run.role == "champion" and config.data.holdout_fraction == 0.0:
        raise ValueError("champion 必须保留内部 holdout")
    if config.validation.folds < 2 or config.validation.tuning_folds < 2:
        raise ValueError("交叉验证折数必须至少为 2")
    if config.validation.stratification_bins < max(config.validation.folds, config.validation.tuning_folds):
        raise ValueError("分层桶数不能少于正式和调参交叉验证折数的最大值")
    if config.training.n_jobs < 1 or config.tuning.trials_per_family < 1:
        raise ValueError("训练并行度和调优次数必须为正数")
    if config.tuning.timeout_seconds_per_family < 0:
        raise ValueError("调优超时不能为负数")
    if config.run.role != "champion" and (
        config.run.log_model or config.evaluation.evaluate_holdout or config.submission.generate
    ):
        raise ValueError("只有 champion 可以评估最终留出集、记录模型或生成提交")
    if config.run.role == "champion" and config.tuning.enabled:
        raise ValueError("champion 必须使用已选定参数，不能再次调优")
    if config.run.role == "champion" and not config.evaluation.evaluate_holdout:
        raise ValueError("champion 必须评估内部 holdout")
    if config.run.role == "champion" and not config.submission.generate:
        raise ValueError("champion 必须生成最终提交产物")
    if config.run.role == "champion" and config.models.selected_parameters_path is None:
        raise ValueError("champion 必须通过 models.selected_parameters_path 固定已选参数")
    if config.run.role == "champion" and not config.models.selected_parameters_path.is_file():
        raise FileNotFoundError(f"champion 已选参数文件不存在: {config.models.selected_parameters_path}")
    if not 0 < config.evaluation.maximum_holdout_rmsle < 1:
        raise ValueError("maximum_holdout_rmsle 必须在 0 和 1 之间")
    if config.models.blend_l2 < 0.0 or not float(config.models.blend_l2) == config.models.blend_l2:
        raise ValueError("models.blend_l2 必须为非负有限值")
    if config.ray.cpus <= 0 or config.ray.memory_bytes <= 0:
        raise ValueError("Ray 资源必须为正数")
    if Path(config.submission.filename).name != config.submission.filename:
        raise ValueError("submission.filename 只能是文件名")
