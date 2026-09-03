"""折内预处理、交叉验证和最终模型拟合。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ray_kaggle_house_prices.config import ProjectConfig
from ray_kaggle_house_prices.data import PreparedDataset, split_features_target
from ray_kaggle_house_prices.evaluate import rmse_log_target
from ray_kaggle_house_prices.models import build_estimator, optimize_blend_weights


@dataclass(frozen=True)
class OofResult:
    family: str
    predictions_log: np.ndarray
    fold_metrics: tuple[dict[str, float], ...]
    mean_rmsle: float
    estimators: tuple[Any, ...]


@dataclass(frozen=True)
class CrossFittedStackResult:
    predictions_log: np.ndarray
    fold_weights: tuple[tuple[float, ...], ...]
    fold_metrics: tuple[dict[str, float], ...]
    mean_rmsle: float


def fold_indices(
    dataset: PreparedDataset,
    config: ProjectConfig,
    *,
    tuning: bool = False,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """从正式清单或独立的确定性调参折分配构造索引。"""

    if tuning and config.validation.tuning_folds != config.validation.folds:
        _, target_log = split_features_target(dataset.development, config.data)
        ranked = pd.Series(target_log).rank(method="first")
        bins = min(config.validation.stratification_bins, max(2, len(ranked) // 20))
        strata = pd.qcut(ranked, q=bins, labels=False, duplicates="drop").to_numpy()
        splitter = __import__("sklearn.model_selection", fromlist=["StratifiedKFold"]).StratifiedKFold(
            n_splits=config.validation.tuning_folds,
            shuffle=True,
            random_state=config.run.seed + 7919,
        )
        return [
            (train_index, validation_index)
            for train_index, validation_index in splitter.split(np.zeros(len(target_log)), strata)
        ]

    folds = dataset.fold_assignments.set_index(config.data.id_column)["fold"]
    fold_values = dataset.development[config.data.id_column].map(folds).to_numpy(dtype="int64")
    return [
        (np.flatnonzero(fold_values != fold), np.flatnonzero(fold_values == fold))
        for fold in sorted(np.unique(fold_values))
    ]


def _fit_rows_without_declared_outliers(frame: pd.DataFrame, config: ProjectConfig) -> pd.DataFrame:
    """只从拟合人群移除预先声明的极端低价大面积异常，验证人群永不修改。"""

    if not config.training.remove_submission_outliers:
        return frame
    if not {"GrLivArea", config.data.target_column}.issubset(frame.columns):
        return frame
    area = pd.to_numeric(frame["GrLivArea"], errors="coerce")
    price = pd.to_numeric(frame[config.data.target_column], errors="coerce")
    mask = ~((area > 4000.0) & (price < 300000.0))
    return frame.loc[mask].reset_index(drop=True)


def fit_oof_family(
    family: str,
    parameters: dict[str, Any],
    dataset: PreparedDataset,
    config: ProjectConfig,
    *,
    tuning: bool = False,
) -> OofResult:
    """在每个折内独立拟合预处理器和模型，返回 OOF 预测。"""

    features, target_log = split_features_target(dataset.development, config.data)
    predictions = np.full(len(features), np.nan, dtype="float64")
    fitted: list[Any] = []
    metrics: list[dict[str, float]] = []
    for fold, (train_index, validation_index) in enumerate(fold_indices(dataset, config, tuning=tuning)):
        fit_frame = _fit_rows_without_declared_outliers(dataset.development.iloc[train_index], config)
        fit_features, fit_target_log = split_features_target(fit_frame, config.data)
        estimator = build_estimator(
            family,
            parameters,
            fit_features,
            seed=config.run.seed + fold,
            n_jobs=config.training.n_jobs,
        )
        estimator.fit(fit_features, fit_target_log)
        validation_prediction = np.asarray(
            estimator.predict(features.iloc[validation_index]), dtype="float64"
        )
        validation_prediction = np.maximum(validation_prediction, 0.0)
        predictions[validation_index] = validation_prediction
        fold_score = rmse_log_target(target_log[validation_index], validation_prediction)
        metrics.append({"fold": float(fold), "rmsle": float(fold_score)})
        fitted.append(estimator)
    if not np.isfinite(predictions).all():
        raise RuntimeError(f"模型 {family} 的 OOF 预测不完整")
    return OofResult(
        family=family,
        predictions_log=predictions,
        fold_metrics=tuple(metrics),
        mean_rmsle=rmse_log_target(target_log, predictions),
        estimators=tuple(fitted),
    )


def fit_cross_fitted_stack(
    family_results: dict[str, OofResult],
    parameters: dict[str, dict[str, Any]],
    dataset: PreparedDataset,
    config: ProjectConfig,
) -> CrossFittedStackResult:
    """用嵌套折外预测评估固定基模型的交叉拟合融合权重。"""

    families = list(config.models.enabled)
    if set(family_results) != set(families):
        raise ValueError("交叉拟合融合的模型族与配置不一致")
    _, target_log = split_features_target(dataset.development, config.data)
    fold_values = dataset.development[config.data.id_column].map(
        dataset.fold_assignments.set_index(config.data.id_column)["fold"]
    ).to_numpy(dtype="int64")
    predictions = np.full(len(target_log), np.nan, dtype="float64")
    fold_weights: list[tuple[float, ...]] = []
    fold_metrics: list[dict[str, float]] = []
    inner_folds = min(config.validation.tuning_folds, max(2, config.validation.folds - 1))
    from sklearn.model_selection import StratifiedKFold

    strata = pd.qcut(
        pd.Series(target_log).rank(method="first"),
        q=min(config.validation.stratification_bins, max(2, len(target_log) // 20)),
        labels=False,
        duplicates="drop",
    ).to_numpy()
    for outer_fold in sorted(np.unique(fold_values)):
        outer_train = np.flatnonzero(fold_values != outer_fold)
        outer_valid = np.flatnonzero(fold_values == outer_fold)
        inner_splitter = StratifiedKFold(
            n_splits=inner_folds,
            shuffle=True,
            random_state=config.run.seed + 7919 + int(outer_fold),
        )
        inner_predictions = np.full((len(outer_train), len(families)), np.nan, dtype="float64")
        for inner_fold, (inner_train_pos, inner_valid_pos) in enumerate(
            inner_splitter.split(outer_train, strata[outer_train])
        ):
            fit_index = outer_train[inner_train_pos]
            valid_index = outer_train[inner_valid_pos]
            fit_frame = _fit_rows_without_declared_outliers(dataset.development.iloc[fit_index], config)
            fit_features, fit_target = split_features_target(fit_frame, config.data)
            valid_features, _ = split_features_target(dataset.development.iloc[valid_index], config.data)
            for family_pos, family in enumerate(families):
                estimator = build_estimator(
                    family,
                    parameters[family],
                    fit_features,
                    seed=config.run.seed + int(outer_fold) * 100 + inner_fold,
                    n_jobs=config.training.n_jobs,
                )
                estimator.fit(fit_features, fit_target)
                inner_predictions[inner_valid_pos, family_pos] = np.maximum(
                    np.asarray(estimator.predict(valid_features), dtype="float64"), 0.0
                )
        if not np.isfinite(inner_predictions).all():
            raise RuntimeError(f"外层折 {outer_fold} 的内层 OOF 预测不完整")
        weights = optimize_blend_weights(inner_predictions, target_log[outer_train], config.models.blend_l2)
        outer_matrix = np.column_stack(
            [np.maximum(
                np.asarray(
                    family_results[family].estimators[
                        int(np.flatnonzero(sorted(np.unique(fold_values)) == outer_fold)[0])
                    ].predict(
                        dataset.development.iloc[outer_valid].drop(
                            columns=[config.data.target_column, config.data.id_column]
                        )
                    ),
                    dtype="float64",
                ),
                0.0,
            ) for family in families]
        )
        predictions[outer_valid] = outer_matrix @ weights
        score = rmse_log_target(target_log[outer_valid], predictions[outer_valid])
        fold_weights.append(tuple(float(item) for item in weights))
        fold_metrics.append({"outer_fold": float(outer_fold), "rmsle": float(score)})
    if not np.isfinite(predictions).all():
        raise RuntimeError("交叉拟合融合预测不完整")
    return CrossFittedStackResult(
        predictions_log=predictions,
        fold_weights=tuple(fold_weights),
        fold_metrics=tuple(fold_metrics),
        mean_rmsle=rmse_log_target(target_log, predictions),
    )


def fit_full_family(
    family: str,
    parameters: dict[str, Any],
    frame: pd.DataFrame,
    config: ProjectConfig,
) -> Any:
    """在给定标注人群上从零拟合一个完整模型管道。"""

    fit_frame = _fit_rows_without_declared_outliers(frame, config)
    features, target_log = split_features_target(fit_frame, config.data)
    estimator = build_estimator(
        family,
        parameters,
        features,
        seed=config.run.seed,
        n_jobs=config.training.n_jobs,
    )
    estimator.fit(features, target_log)
    return estimator


def predict_log(estimator: Any, frame: pd.DataFrame, config: ProjectConfig) -> np.ndarray:
    """执行严格的推理预处理回放，并限制对数预测为有效价格范围。"""

    features = frame.drop(columns=[config.data.target_column, config.data.id_column], errors="ignore")
    if config.data.target_column in features or config.data.id_column in features:
        raise RuntimeError("推理特征仍包含目标或 Id")
    prediction = np.asarray(estimator.predict(features), dtype="float64")
    if not np.isfinite(prediction).all():
        raise ValueError("模型产生非有限预测")
    return np.maximum(prediction, 0.0)
