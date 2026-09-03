"""用于十分类手写数字识别的轻量级卷积网络。"""

from __future__ import annotations

from typing import Any

import torch
from torch import nn


class HandwrittenDigitsCNN(nn.Module):
    """模块级模型类保证 Ray 序列化和 MLflow 模型回读稳定。"""

    def __init__(self, dense_units: int, dropout: float, optimizer_name: str, learning_rate: float) -> None:
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 32, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(64, 128, 3, padding=1),
            nn.ReLU(inplace=True),
        )
        self.pool = nn.AdaptiveAvgPool2d((4, 4))
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Dropout(dropout) if dropout else nn.Identity(),
            nn.Linear(128 * 4 * 4, dense_units),
            nn.ReLU(inplace=True),
            nn.Linear(dense_units, 10),
        )
        self.family = "digit_cnn"
        self.num_classes = 10
        self.optimizer_name = optimizer_name
        self.learning_rate = float(learning_rate)

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        return self.classifier(self.pool(self.features(inputs)))

    def count_params(self) -> int:
        return sum(parameter.numel() for parameter in self.parameters())


def build_model(model_config: dict[str, Any], training_config: dict[str, Any], image_size: tuple[int, int], seed: int) -> HandwrittenDigitsCNN:
    del image_size
    torch.manual_seed(seed)
    if model_config["family"] != "digit_cnn":
        raise ValueError(f"不支持的手写数字模型: {model_config['family']}")
    return HandwrittenDigitsCNN(
        int(model_config["dense_units"]),
        float(model_config["dropout"]),
        str(training_config["optimizer"]),
        float(training_config["learning_rate"]),
    )
