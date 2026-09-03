"""评估本地模型与公开预测文件的非治理实验，不写入正式提交。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path("/data/ai/chenzhangyue/code/galatea")
LOCAL = BASE / "platform-data/ray-kaggle-house-prices/outputs/optimized-ohe-submission.csv"
PUBLIC = Path("/tmp/kaggle-top-output/best_submission.csv")
OUT = BASE / "platform-data/ray-kaggle-house-prices/outputs/public-blend-experiment.csv"


def main() -> None:
    local = pd.read_csv(LOCAL)
    public = pd.read_csv(PUBLIC)
    if not local["Id"].equals(public["Id"]):
        raise ValueError("两个预测文件的 Id 顺序不一致")
    local_log = np.log(np.clip(local["SalePrice"].to_numpy(dtype="float64"), 1.0, None))
    public_log = np.log(np.clip(public["SalePrice"].to_numpy(dtype="float64"), 1.0, None))
    # 仅记录候选混合的分布，不能从公共排行榜反馈中选择或发布。
    for weight in (0.0, 0.25, 0.5, 0.75, 1.0):
        prediction = np.exp((1.0 - weight) * local_log + weight * public_log)
        print(weight, float(np.min(prediction)), float(np.median(prediction)), float(np.max(prediction)))
    print("local_sha256", hashlib.sha256(LOCAL.read_bytes()).hexdigest())
    print("public_sha256", hashlib.sha256(PUBLIC.read_bytes()).hexdigest())
    print("不生成正式提交：公开预测文件仅用于审计，不纳入治理训练链路。")


if __name__ == "__main__":
    main()
