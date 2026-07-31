"""PyTorch model families for binary cat and dog classification."""

from __future__ import annotations

from typing import Any

import torch
from torch import nn


class ImageNetNormalize(nn.Module):
    """Apply the normalization expected by torchvision ImageNet backbones."""

    def __init__(self) -> None:
        super().__init__()
        self.register_buffer(
            "mean", torch.tensor((0.485, 0.456, 0.406)).view(1, 3, 1, 1)
        )
        self.register_buffer(
            "std", torch.tensor((0.229, 0.224, 0.225)).view(1, 3, 1, 1)
        )

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        return (inputs - self.mean) / self.std


class CatsDogsClassifier(nn.Module):
    """Backbone plus binary classifier with a module-level pickle identity.

    Keeping this class at module scope makes both Ray's controller serialization and
    MLflow's PyTorch flavor independent of a transient ``build_model`` call frame.
    """

    def __init__(
        self,
        features: nn.Module,
        feature_channels: int,
        dense_units: int,
        dropout: float,
        *,
        normalize: nn.Module | None = None,
        family: str,
        optimizer_name: str,
        learning_rate: float,
    ) -> None:
        super().__init__()
        self.normalize = normalize if normalize is not None else nn.Identity()
        self.features = features
        self.pool = nn.AdaptiveAvgPool2d((1, 1))
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Dropout(dropout) if dropout else nn.Identity(),
            nn.Linear(feature_channels, dense_units),
            nn.ReLU(inplace=True),
            nn.Linear(dense_units, 2),
        )
        self.family = family
        self.optimizer_name = optimizer_name
        self.learning_rate = float(learning_rate)

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        return self.classifier(self.pool(self.features(self.normalize(inputs))))

    def count_params(self) -> int:
        return sum(parameter.numel() for parameter in self.parameters())


def build_model(
    model_config: dict[str, Any],
    training_config: dict[str, Any],
    image_size: tuple[int, int],
    seed: int,
) -> Any:
    from torchvision import models

    del image_size
    torch.manual_seed(seed)

    family = model_config["family"]
    normalize: nn.Module = nn.Identity()
    if family == "custom_cnn":
        feature_layers: list[nn.Module] = []
        channels = (3, 32, 64, 128, 256)
        for input_channels, output_channels in zip(channels, channels[1:]):
            feature_layers.extend(
                [
                    nn.Conv2d(input_channels, output_channels, 3, padding=1),
                    nn.ReLU(inplace=True),
                    nn.MaxPool2d(2),
                ]
            )
        features = nn.Sequential(*feature_layers)
        feature_channels = 256
    elif family == "mobilenet_v2":
        weights = (
            models.MobileNet_V2_Weights.DEFAULT
            if model_config["pretrained_weights"] == "imagenet"
            else None
        )
        backbone = models.mobilenet_v2(weights=weights)
        features = backbone.features
        feature_channels = 1280
        normalize = ImageNetNormalize()
        for parameter in features.parameters():
            parameter.requires_grad = False
    else:
        raise ValueError(f"Unsupported model family: {family}")

    return CatsDogsClassifier(
        features,
        feature_channels,
        int(model_config["dense_units"]),
        float(model_config["dropout"]),
        normalize=normalize,
        family=family,
        optimizer_name=str(training_config["optimizer"]),
        learning_rate=float(training_config["learning_rate"]),
    )
