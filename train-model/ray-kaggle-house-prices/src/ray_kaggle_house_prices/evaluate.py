"""House Prices 指标与预测验证。"""

from __future__ import annotations

import numpy as np


def rmsle(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """按 Kaggle 定义计算非负价格的均方根对数误差。"""

    truth = np.asarray(y_true, dtype="float64")
    prediction = np.asarray(y_pred, dtype="float64")
    if truth.shape != prediction.shape:
        raise ValueError("真实值与预测值形状不一致")
    if not np.isfinite(truth).all() or not np.isfinite(prediction).all():
        raise ValueError("RMSLE 输入包含非有限值")
    if (truth < 0).any():
        raise ValueError("真实价格不能为负数")
    prediction = np.clip(prediction, 0.0, None)
    return float(np.sqrt(np.mean(np.square(np.log1p(prediction) - np.log1p(truth)))))


def rmse_log_target(y_log_true: np.ndarray, y_log_pred: np.ndarray) -> float:
    """在 log1p 目标空间计算与 RMSLE 等价的 RMSE。"""

    truth = np.asarray(y_log_true, dtype="float64")
    prediction = np.asarray(y_log_pred, dtype="float64")
    if truth.shape != prediction.shape:
        raise ValueError("对数目标与预测值形状不一致")
    if not np.isfinite(truth).all() or not np.isfinite(prediction).all():
        raise ValueError("对数目标或预测包含非有限值")
    return float(np.sqrt(np.mean(np.square(prediction - truth))))
