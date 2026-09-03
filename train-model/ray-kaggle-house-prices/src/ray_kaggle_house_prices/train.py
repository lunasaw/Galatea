"""House Prices 训练编排、调优、MLflow 记录和提交产物。"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path
from typing import Any

import joblib
import mlflow
import numpy as np
import pandas as pd

from ray_kaggle_house_prices.config import ProjectConfig
from ray_kaggle_house_prices.data import PreparedDataset, prepare_dataset, split_features_target
from ray_kaggle_house_prices.evaluate import rmsle, rmse_log_target
from ray_kaggle_house_prices.input_pipeline import OofResult, fit_full_family, fit_oof_family, predict_log
from ray_kaggle_house_prices.integrity import build_integrity_report
from ray_kaggle_house_prices.models import HousePriceEnsemble, optimize_blend_weights
from ray_kaggle_house_prices.runtime import execution_provenance
from ray_kaggle_house_prices.tracking import (
    code_identity,
    idempotency_key,
    inspect_tracking,
    log_run_inputs,
    preflight_tracking,
    verify_artifact_roundtrip,
)


def config_plan(config: ProjectConfig) -> dict[str, Any]:
    """输出不访问数据的配置计划。"""

    return {
        "config": config.as_dict(),
        "config_digest": config.config_digest,
        "objective": {
            "metric": config.training.objective_metric,
            "mode": config.training.objective_mode,
            "test_used_for_selection": False,
            "uses_test_holdout": False,
            "competition_test_is_inference_only": True,
        },
        "requested_resources": {
            "ray_address": config.ray.address,
            "cpus": config.ray.cpus,
            "memory_bytes": config.ray.memory_bytes,
        },
    }


def _selected_parameters(config: ProjectConfig) -> tuple[dict[str, dict[str, Any]], np.ndarray | None]:
    path_value = config.models.selected_parameters_path
    if path_value is None:
        return {family: dict(config.models.parameters[family]) for family in config.models.enabled}, None
    if not path_value.is_file():
        raise FileNotFoundError(f"selected parameter 文件不存在: {path_value}")
    payload = json.loads(path_value.read_text(encoding="utf-8"))
    if payload.get("project") != config.project_name:
        raise ValueError(f"selected parameter 文件项目不匹配: {path_value}")
    if payload.get("objective") != "val_rmsle/min":
        raise ValueError(f"selected parameter 文件目标不匹配: {path_value}")
    expected_order = list(config.models.enabled)
    if payload.get("family_order") not in (None, expected_order):
        raise ValueError(f"selected parameter 文件模型顺序不匹配: {path_value}")
    if payload.get("preprocessing_version") not in (None, config.data.preprocessing_version):
        raise ValueError(f"selected parameter 文件预处理版本不匹配: {path_value}")
    if payload.get("remove_submission_outliers") not in (None, config.training.remove_submission_outliers):
        raise ValueError(f"selected parameter 文件异常值策略不匹配: {path_value}")
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError(f"selected parameter 文件缺少 parameters: {path_value}")
    missing = [family for family in config.models.enabled if family not in parameters or not isinstance(parameters[family], dict)]
    if missing:
        raise ValueError(f"selected parameter 文件缺少模型族: {missing}")
    stored_weights = payload.get("blend_weights")
    if stored_weights is not None:
        if set(stored_weights) != set(expected_order):
            raise ValueError(f"selected parameter 文件融合权重模型族不匹配: {path_value}")
        weights = np.asarray([stored_weights[family] for family in expected_order], dtype="float64")
        if not np.isfinite(weights).all() or (weights < 0).any() or not np.isclose(weights.sum(), 1.0, atol=1e-6):
            raise ValueError(f"selected parameter 文件融合权重非法: {path_value}")
    selected = {family: dict(parameters[family]) for family in config.models.enabled}
    return selected, weights if stored_weights is not None else None


def read_only_plan(config: ProjectConfig) -> dict[str, Any]:
    """执行训练前的配置、数据架构、泄漏与远端 Artifact 检查。"""

    dataset = prepare_dataset(
        config.data,
        config.validation,
        config.run.seed,
        include_holdout=config.run.role == "champion" and config.evaluation.evaluate_holdout,
        include_inference=config.run.role == "champion" and config.submission.generate,
    )
    integrity = build_integrity_report(
        config,
        dataset,
        include_holdout=config.run.role == "champion" and config.evaluation.evaluate_holdout,
        include_inference=config.run.role == "champion" and config.submission.generate,
    )
    tracking = inspect_tracking(config)
    code = code_identity(config)
    identity = idempotency_key(config, dataset, code, integrity["integrity_digest"])
    return {
        **config_plan(config),
        "tracking": tracking,
        "dataset": {
            "version": dataset.dataset_version,
            "content_sha256": dataset.content_digest,
            "split_sha256": dataset.split_digest,
            "manifest_path": str(dataset.manifest_path),
            "profile": dataset.profile,
        },
        "integrity": integrity,
        "code": code,
        "idempotency_key": identity,
        "will_read_competition_test": config.run.role == "champion" and config.submission.generate,
        "will_evaluate_internal_holdout": config.run.role == "champion" and config.evaluation.evaluate_holdout,
    }


def _suggest_parameters(trial: Any, family: str, base: dict[str, Any]) -> dict[str, Any]:
    """为每个模型族生成受约束的验证集搜索空间。"""

    params = dict(base)
    if family == "elastic_net":
        params.update(
            alpha=trial.suggest_float("alpha", 1e-5, 5e-3, log=True),
            l1_ratio=trial.suggest_float("l1_ratio", 0.70, 0.99),
        )
    elif family == "lasso":
        params.update(alpha=trial.suggest_float("alpha", 1e-5, 2e-3, log=True))
    elif family == "ridge":
        params.update(alpha=trial.suggest_float("alpha", 0.5, 80.0, log=True))
    elif family == "kernel_ridge":
        params.update(
            alpha=trial.suggest_float("alpha", 1e-4, 20.0, log=True),
            kernel=trial.suggest_categorical("kernel", ["polynomial", "rbf"]),
            gamma=trial.suggest_float("gamma", 1e-5, 0.05, log=True),
        )
    elif family == "svr":
        params.update(
            C=trial.suggest_float("C", 1e-2, 30.0, log=True),
            epsilon=trial.suggest_float("epsilon", 1e-3, 0.2, log=True),
            gamma=trial.suggest_float("gamma", 1e-5, 0.05, log=True),
            kernel=trial.suggest_categorical("kernel", ["rbf", "poly"]),
        )
    elif family == "gradient_boosting":
        params.update(
            n_estimators=trial.suggest_int("n_estimators", 500, 2500, step=250),
            learning_rate=trial.suggest_float("learning_rate", 0.015, 0.08, log=True),
            max_depth=trial.suggest_int("max_depth", 2, 4),
            min_samples_leaf=trial.suggest_int("min_samples_leaf", 3, 20),
            max_features=trial.suggest_categorical("max_features", ["sqrt", None]),
            loss=trial.suggest_categorical("loss", ["huber", "squared_error"]),
        )
    elif family == "xgboost":
        params.update(
            n_estimators=trial.suggest_int("n_estimators", 500, 2500, step=250),
            learning_rate=trial.suggest_float("learning_rate", 0.01, 0.05, log=True),
            max_depth=trial.suggest_int("max_depth", 2, 4),
            min_child_weight=trial.suggest_int("min_child_weight", 1, 8),
            subsample=trial.suggest_float("subsample", 0.70, 1.0),
            colsample_bytree=trial.suggest_float("colsample_bytree", 0.65, 1.0),
            reg_alpha=trial.suggest_float("reg_alpha", 1e-6, 0.1, log=True),
            reg_lambda=trial.suggest_float("reg_lambda", 0.5, 10.0, log=True),
        )
    elif family == "lightgbm":
        params.update(
            n_estimators=trial.suggest_int("n_estimators", 500, 2500, step=250),
            learning_rate=trial.suggest_float("learning_rate", 0.01, 0.05, log=True),
            num_leaves=trial.suggest_int("num_leaves", 8, 31),
            min_child_samples=trial.suggest_int("min_child_samples", 8, 30),
            subsample=trial.suggest_float("subsample", 0.70, 1.0),
            colsample_bytree=trial.suggest_float("colsample_bytree", 0.65, 1.0),
            reg_alpha=trial.suggest_float("reg_alpha", 1e-6, 0.1, log=True),
            reg_lambda=trial.suggest_float("reg_lambda", 0.01, 2.0, log=True),
        )
    elif family in {"catboost", "catboost_native"}:
        params.update(
            iterations=trial.suggest_int("iterations", 750, 3500, step=250),
            learning_rate=trial.suggest_float("learning_rate", 0.015, 0.06, log=True),
            depth=trial.suggest_int("depth", 4, 7),
            l2_leaf_reg=trial.suggest_float("l2_leaf_reg", 2.0, 20.0, log=True),
            random_strength=trial.suggest_float("random_strength", 0.0, 1.5),
        )
    return params


def _tune_family(family: str, base: dict[str, Any], dataset: PreparedDataset, config: ProjectConfig) -> tuple[dict[str, Any], float, list[dict[str, Any]]]:
    """只使用开发集的 tuning folds 近似进行模型族调优。"""

    if not config.tuning.enabled:
        result = fit_oof_family(family, base, dataset, config)
        return dict(base), result.mean_rmsle, [{"number": 0, "value": result.mean_rmsle, "params": dict(base)}]
    import optuna

    trial_config = config
    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(
            seed=config.run.seed
            + int(hashlib.sha256(family.encode("utf-8")).hexdigest()[:8], 16) % 1000
        ),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=3),
    )
    started = time.perf_counter()

    def objective(trial: Any) -> float:
        parameters = _suggest_parameters(trial, family, base)
        result = fit_oof_family(family, parameters, dataset, trial_config, tuning=True)
        trial.set_user_attr("fold_metrics", list(result.fold_metrics))
        return result.mean_rmsle

    study.optimize(
        objective,
        n_trials=config.tuning.trials_per_family,
        timeout=config.tuning.timeout_seconds_per_family or None,
        show_progress_bar=False,
    )
    if not study.best_trials:
        return dict(base), float("inf"), []
    best = dict(base)
    best.update(study.best_trial.params)
    trials = [
        {"number": trial.number, "value": trial.value, "params": trial.params}
        for trial in study.trials
        if trial.value is not None
    ]
    print(
        json.dumps(
            {"event": "family-tuned", "family": family, "best_rmsle": study.best_value, "seconds": time.perf_counter() - started},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return best, float(study.best_value), trials


def _fit_selected_oof(dataset: PreparedDataset, config: ProjectConfig, parameters: dict[str, dict[str, Any]]) -> tuple[dict[str, OofResult], np.ndarray, np.ndarray, np.ndarray]:
    results: dict[str, OofResult] = {}
    for family in config.models.enabled:
        print(json.dumps({"event": "family-oof-started", "family": family}, ensure_ascii=False), flush=True)
        results[family] = fit_oof_family(family, parameters[family], dataset, config)
        print(json.dumps({"event": "family-oof-finished", "family": family, "rmsle": results[family].mean_rmsle}, ensure_ascii=False), flush=True)
    matrix = np.column_stack([results[family].predictions_log for family in config.models.enabled])
    _, target_log = split_features_target(dataset.development, config.data)
    weights = optimize_blend_weights(matrix, target_log, config.models.blend_l2)
    blend_prediction = matrix @ weights
    return results, matrix, target_log, weights


def _save_selected_parameters(config: ProjectConfig, parameters: dict[str, dict[str, Any]], family_scores: dict[str, float], weights: np.ndarray) -> Path:
    config.data.output_dir.mkdir(parents=True, exist_ok=True)
    path = config.data.output_dir / "selected-model-parameters.json"
    payload = {
        "project": config.project_name,
        "config_digest": config.config_digest,
        "objective": "val_rmsle/min",
        "family_order": list(config.models.enabled),
        "preprocessing_version": config.data.preprocessing_version,
        "holdout_fraction": config.data.holdout_fraction,
        "remove_submission_outliers": config.training.remove_submission_outliers,
        "parameters": parameters,
        "family_validation_rmsle": family_scores,
        "blend_weights": {family: float(weight) for family, weight in zip(config.models.enabled, weights)},
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != serialized:
        path = config.data.output_dir / f"selected-model-parameters-{hashlib.sha256(serialized.encode()).hexdigest()[:12]}.json"
    path.write_text(serialized, encoding="utf-8")
    return path


def _log_model(ensemble: HousePriceEnsemble, config: ProjectConfig, run_id: str) -> str:
    """记录 MLflow 模型并用 Artifact API 验证 MLmodel 描述文件。"""

    model_info = mlflow.sklearn.log_model(
        sk_model=ensemble,
        name="model",
        serialization_format="cloudpickle",
        metadata={
            "project": config.project_name,
            "preprocessing_version": config.data.preprocessing_version,
            "target_transform": "log1p during fit; expm1 during inference",
            "output_semantics": "nonnegative SalePrice",
        },
    )
    with tempfile.TemporaryDirectory(prefix="house-prices-model-check-") as directory:
        model_root = Path(mlflow.artifacts.download_artifacts(artifact_uri=model_info.model_uri, dst_path=directory))
        descriptor = model_root / "MLmodel"
        if not descriptor.is_file():
            raise RuntimeError("MLflow 模型缺少 model/MLmodel")
        mlflow.log_artifacts(model_root, artifact_path="model")
    return f"runs:/{run_id}/model"


def _log_submission(ensemble: HousePriceEnsemble, dataset: PreparedDataset, config: ProjectConfig) -> dict[str, Any]:
    if dataset.inference is None:
        raise RuntimeError("提交生成需要读取 Kaggle 无标签推理集")
    prediction_matrix = np.column_stack(
        [predict_log(estimator, dataset.inference, config) for estimator in ensemble.estimators]
    )
    weights = np.asarray(ensemble.weights, dtype="float64")
    prediction_log = np.maximum(prediction_matrix @ weights, 0.0)
    predictions = np.clip(np.expm1(prediction_log), 0.0, None)
    submission = pd.DataFrame({config.data.id_column: dataset.inference[config.data.id_column].astype(int), config.data.target_column: predictions})
    sample = pd.read_csv(config.data.sample_submission_csv)
    if list(submission.columns) != [config.data.id_column, config.data.target_column] or len(submission) != len(sample):
        raise ValueError("提交文件架构与 sample_submission 不一致")
    if not submission[config.data.id_column].reset_index(drop=True).equals(sample[config.data.id_column].astype(int).reset_index(drop=True)):
        raise ValueError("提交文件 Id 顺序与 sample_submission 不一致")
    if not np.isfinite(predictions).all() or (predictions < 0).any():
        raise ValueError("提交预测包含非法价格")
    config.data.output_dir.mkdir(parents=True, exist_ok=True)
    path = config.data.output_dir / config.submission.filename
    submission.to_csv(path, index=False)
    return {
        "path": str(path),
        "rows": len(submission),
        "prediction_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "prediction_min": float(predictions.min()),
        "prediction_max": float(predictions.max()),
    }


def run_training(config: ProjectConfig, *, force: bool = False) -> dict[str, Any]:
    """运行一次可追踪、可复现且不自动晋级的训练。"""

    dataset = prepare_dataset(
        config.data,
        config.validation,
        config.run.seed,
        include_holdout=config.run.role == "champion" and config.evaluation.evaluate_holdout,
        include_inference=config.run.role == "champion" and config.submission.generate,
    )
    integrity = build_integrity_report(
        config,
        dataset,
        include_holdout=config.run.role == "champion" and config.evaluation.evaluate_holdout,
        include_inference=config.run.role == "champion" and config.submission.generate,
    )
    code = code_identity(config)
    identity = idempotency_key(config, dataset, code, integrity["integrity_digest"])
    tracking = preflight_tracking(config)
    client = mlflow.MlflowClient()
    previous = client.search_runs([tracking["experiment_id"]], filter_string=f"tags.idempotency_key = '{identity}'", order_by=["attributes.start_time DESC"])
    successful = next((item for item in previous if item.info.status == "FINISHED" and item.data.tags.get("run.outcome") == "succeeded"), None)
    if successful is not None and not force:
        return {"status": "already-succeeded", "run_id": successful.info.run_id, "idempotency_key": identity, "training_started": False}

    with mlflow.start_run(
        experiment_id=tracking["experiment_id"],
        run_name=f"{config.run.name_prefix}-{config.run.role}-{identity[:8]}",
        tags={
            "project": config.project_name,
            "run.role": config.run.role,
            "run.outcome": "running",
            "lifecycle.stage": "training-optimization",
            "idempotency_key": identity,
            "dataset_version": dataset.dataset_version,
            "test.evaluated": "false",
            "competition.test.used_for_selection": "false",
            "registry.promotion": "manual-only",
            **execution_provenance(config.project_name),
        },
        description="House Prices log-target tabular regression with deterministic folds and driver-owned tracking",
        log_system_metrics=True,
    ) as active_run:
        run_id = active_run.info.run_id
        print(json.dumps({"event": "run-started", "mlflow_run_id": run_id, "idempotency_key": identity}, ensure_ascii=False), flush=True)
        try:
            log_run_inputs(config, dataset, code, integrity)
            selected, stored_weights = _selected_parameters(config)
            tuning_records: dict[str, list[dict[str, Any]]] = {}
            family_scores: dict[str, float] = {}
            if config.tuning.enabled:
                tuned: dict[str, dict[str, Any]] = {}
                for family in config.models.enabled:
                    tuned[family], family_scores[family], tuning_records[family] = _tune_family(family, selected[family], dataset, config)
                selected = tuned
            oof_results, matrix, target_log, weights = _fit_selected_oof(dataset, config, selected)
            if stored_weights is not None:
                weights = stored_weights
            blend_log_prediction = np.maximum(matrix @ weights, 0.0)
            blend_rmsle = rmse_log_target(target_log, blend_log_prediction)
            family_scores = {family: float(oof_results[family].mean_rmsle) for family in config.models.enabled}
            mlflow.log_metrics({"val_rmsle": blend_rmsle, **{f"val_rmsle_{family}": score for family, score in family_scores.items()}})
            mlflow.log_dict(
                {
                    "selection_metric": "val_rmsle",
                    "selection_mode": "min",
                    "best_metric": blend_rmsle,
                    "family_validation_rmsle": family_scores,
                    "blend_weights": {family: float(weight) for family, weight in zip(config.models.enabled, weights)},
                    "parameters": selected,
                    "test_evaluated_during_selection": False,
                    "tuning_trials": tuning_records,
                },
                "reports/model-selection.json",
            )
            oof_frame = pd.DataFrame({"Id": dataset.development[config.data.id_column].astype(int), "actual_log": target_log, "blend_pred_log": blend_log_prediction})
            for index, family in enumerate(config.models.enabled):
                oof_frame[f"pred_log_{family}"] = matrix[:, index]
            mlflow.log_table(oof_frame, "outputs/oof-predictions.json")
            selected_path = _save_selected_parameters(config, selected, family_scores, weights)
            mlflow.log_artifact(str(selected_path), artifact_path="reports")

            full_estimators = tuple(
                fit_full_family(family, selected[family], dataset.development, config)
                for family in config.models.enabled
            )
            dev_ensemble = HousePriceEnsemble(
                families=tuple(config.models.enabled),
                estimators=full_estimators,
                weights=tuple(float(item) for item in weights),
                preprocessing_version=config.data.preprocessing_version,
            )
            holdout_metrics: dict[str, float] = {}
            if config.run.role == "champion" and config.evaluation.evaluate_holdout and dataset.holdout is not None:
                holdout_matrix = np.column_stack([predict_log(estimator, dataset.holdout, config) for estimator in full_estimators])
                holdout_log = np.maximum(holdout_matrix @ weights, 0.0)
                holdout_metrics = {"holdout_rmsle": rmsle(dataset.holdout[config.data.target_column].to_numpy(dtype="float64"), np.expm1(holdout_log))}
                mlflow.log_metrics(holdout_metrics)
                holdout_passed = holdout_metrics["holdout_rmsle"] <= config.evaluation.maximum_holdout_rmsle
                mlflow.log_dict(
                    {
                        "evaluation_population": "internal_labeled_holdout_from_kaggle_train",
                        "competition_test_evaluated": False,
                        "metrics": holdout_metrics,
                        "quality_gate": {
                            "maximum_holdout_rmsle": config.evaluation.maximum_holdout_rmsle,
                            "passed": holdout_passed,
                        },
                    },
                    "reports/final-test-evaluation.json",
                )
                mlflow.set_tag("holdout.evaluated", "true")
                mlflow.set_tag("quality.holdout_passed", str(holdout_passed).lower())
                if not holdout_passed:
                    raise RuntimeError(
                        f"内部 holdout RMSLE 未通过质量门槛: {holdout_metrics['holdout_rmsle']:.6f} > "
                        f"{config.evaluation.maximum_holdout_rmsle:.6f}"
                    )
            submission = None
            if config.run.role == "champion" and config.submission.generate:
                all_estimators = tuple(
                    fit_full_family(family, selected[family], dataset.all_labeled, config)
                    for family in config.models.enabled
                )
                final_ensemble = HousePriceEnsemble(
                    families=tuple(config.models.enabled),
                    estimators=all_estimators,
                    weights=tuple(float(item) for item in weights),
                    preprocessing_version=config.data.preprocessing_version,
                )
                submission = _log_submission(final_ensemble, dataset, config)
                mlflow.log_dict(submission, "outputs/submission-metadata.json")
                mlflow.log_artifact(submission["path"], artifact_path="outputs")
                model_ensemble = final_ensemble
            else:
                model_ensemble = dev_ensemble
            model_uri = None
            if config.run.log_model:
                model_uri = _log_model(model_ensemble, config, run_id)
                mlflow.set_tag("model.uri", model_uri)
            artifact_digest = verify_artifact_roundtrip(run_id, "reports/model-selection.json")
            mlflow.set_tag("artifact.roundtrip_verified", "true")
            mlflow.set_tag("run.outcome", "succeeded")
            mlflow.set_tag("artifact.model_selection_sha256", artifact_digest)
            return {
                "status": "succeeded",
                "run_id": run_id,
                "artifact_uri": active_run.info.artifact_uri,
                "model_uri": model_uri,
                "dataset_version": dataset.dataset_version,
                "idempotency_key": identity,
                "val_rmsle": blend_rmsle,
                "family_validation_rmsle": family_scores,
                "blend_weights": {family: float(weight) for family, weight in zip(config.models.enabled, weights)},
                "holdout_metrics": holdout_metrics,
                "submission": submission,
                "selected_parameters_path": str(selected_path),
                "training_started": True,
            }
        except BaseException as error:
            mlflow.set_tags({"run.outcome": "failed", "failure.type": type(error).__name__})
            raise
