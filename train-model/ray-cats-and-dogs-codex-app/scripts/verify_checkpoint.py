#!/usr/bin/env python3
"""Forward-only recovery check for a checkpoint retrieved from MLflow."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import torch
from ray_cats_dogs.models import build_model


def main() -> None:
    torch.set_num_threads(2)
    state = torch.load(sys.argv[1], map_location="cpu", weights_only=True)
    size = tuple(state["image_size"])
    model = build_model(
        state["model_config"], state["training_config"], size, int(state["seed"])
    )
    model.load_state_dict(state["model_state_dict"], strict=True)
    model.eval()
    with torch.inference_mode():
        logits = model(torch.zeros(1, 3, *size))
    if logits.shape != (1, 2) or not torch.isfinite(logits).all():
        raise RuntimeError("Recovered model failed its output contract")
    print("Checkpoint fresh-process load and finite forward output verified")


if __name__ == "__main__":
    main()
