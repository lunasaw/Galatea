#!/usr/bin/env python3
"""使用全量开发集 OOF 选出的权重重新拟合并生成合法候选提交。"""

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
    dataset = prepare_dataset(
        config.data,
        config.validation,
        config.run.seed,
        include_holdout=False,
        include_inference=True,
    )
    selected_path = config.data.output_dir / "selected-model-parameters-69483ed4d1b5.json"
    payload = json.loads(selected_path.read_text(encoding="utf-8"))
    parameters = payload["parameters"]
    weights_payload = payload["blend_weights"]
    families = list(config.models.enabled)
    if set(parameters) < set(families) or set(weights_payload) != set(families):
        raise ValueError("全量 OOF 参数文件与配置模型族不一致")
    weights = np.asarray([weights_payload[family] for family in families], dtype="float64")
    if not np.isfinite(weights).all() or (weights < 0).any() or not np.isclose(weights.sum(), 1.0):
        raise ValueError("全量 OOF 融合权重非法")

    estimators = tuple(
        fit_full_family(family, parameters[family], dataset.all_labeled, config)
        for family in families
    )
    matrix = np.column_stack(
        [predict_log(estimator, dataset.inference, config) for estimator in estimators]
    )
    prediction_log = np.maximum(matrix @ weights, 0.0)
    prediction = np.clip(np.expm1(prediction_log), 0.0, None)
    if not np.isfinite(prediction).all():
        raise ValueError("生成了非有限预测")
    sample = pd.read_csv(config.data.sample_submission_csv)
    submission = pd.DataFrame({config.data.id_column: dataset.inference[config.data.id_column].astype("int64"), config.data.target_column: prediction})
    if not submission[config.data.id_column].reset_index(drop=True).equals(sample[config.data.id_column].astype("int64").reset_index(drop=True)):
        raise ValueError("生成提交的 Id 顺序与 sample_submission 不一致")
    output = config.data.output_dir / "full-oof-weight-submission.csv"
    submission.to_csv(output, index=False)
    print(json.dumps({
        "path": str(output),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "rows": len(submission),
        "min": float(prediction.min()),
        "median": float(np.median(prediction)),
        "max": float(prediction.max()),
        "families": families,
        "weights": weights.tolist(),
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
