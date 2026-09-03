#!/usr/bin/env python3
"""根据已验证的全量 OOF 保守权重生成候选提交，不读取测试标签。"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_kaggle_house_prices.config import load_config  # noqa: E402
from ray_kaggle_house_prices.data import prepare_dataset  # noqa: E402
from ray_kaggle_house_prices.input_pipeline import fit_full_family, predict_log  # noqa: E402


def main() -> None:
    config = load_config(PROJECT_ROOT / "configs" / "optimized-ohe.yaml")
    dataset = prepare_dataset(config.data, config.validation, config.run.seed, include_holdout=False, include_inference=True)
    source = json.loads((config.data.output_dir / "selected-model-parameters-69483ed4d1b5.json").read_text(encoding="utf-8"))
    families = list(config.models.enabled)
    parameters = source["parameters"]
    # 嵌套折外评估显示模型排序稳定，预先登记偏向 EN/CatBoost/XGBoost 的保守权重。
    raw_weights = {"ridge": 0.10, "elastic_net": 0.45, "gradient_boosting": 0.08, "xgboost": 0.17, "lightgbm": 0.05, "catboost": 0.15}
    weights = np.asarray([raw_weights[name] for name in families], dtype="float64")
    estimators = tuple(fit_full_family(name, parameters[name], dataset.all_labeled, config) for name in families)
    matrix = np.column_stack([predict_log(estimator, dataset.inference, config) for estimator in estimators])
    prediction = np.clip(np.expm1(np.maximum(matrix @ weights, 0.0)), 0.0, None)
    sample = pd.read_csv(config.data.sample_submission_csv)
    submission = pd.DataFrame({"Id": dataset.inference["Id"].astype("int64"), "SalePrice": prediction})
    if not submission["Id"].reset_index(drop=True).equals(sample["Id"].astype("int64").reset_index(drop=True)):
        raise ValueError("Id 顺序与 sample_submission 不一致")
    if not np.isfinite(prediction).all() or (prediction < 0).any():
        raise ValueError("预测值非法")
    output = config.data.output_dir / "conservative-stack-submission.csv"
    submission.to_csv(output, index=False)
    print(json.dumps({"path": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "weights": raw_weights, "min": float(prediction.min()), "median": float(np.median(prediction)), "max": float(prediction.max())}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
