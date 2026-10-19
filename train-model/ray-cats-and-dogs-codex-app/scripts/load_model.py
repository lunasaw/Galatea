"""Recover a trusted Run through Artifact APIs; no repository imports or test reads."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlsplit


def load_run_model(
    tracking_uri: str, run_id: str, checkpoint_sha256: str, output_dir: Path
) -> tuple[Any, dict[str, Any]]:
    """Return the verified CPU model and recovery metadata for application use."""
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Expected an MLflow Run ID")
    if not re.fullmatch(r"[a-f0-9]{64}", checkpoint_sha256):
        raise ValueError("Expected the frozen checkpoint SHA-256")
    address = urlsplit(tracking_uri)
    if (
        address.scheme not in {"http", "https"}
        or not address.hostname
        or address.username is not None
        or address.password is not None
        or address.query
        or address.fragment
    ):
        raise ValueError("Expected an explicit HTTP(S) Tracking URI without credentials")
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    import mlflow
    import mlflow.pytorch
    from mlflow import MlflowClient
    import torch

    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)
    output_dir = output_dir.resolve()
    model_destination = output_dir / "model-download"
    checkpoint_destination = output_dir / "checkpoint-download"
    model_destination.mkdir(parents=True, exist_ok=True)
    checkpoint_destination.mkdir(parents=True, exist_ok=True)
    model_path = Path(client.download_artifacts(run_id, "model", str(model_destination)))
    checkpoint_path = Path(client.download_artifacts(
        run_id, "checkpoints/best/best-model.pt", str(checkpoint_destination)
    ))
    with checkpoint_path.open("rb") as stream:
        actual_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual_sha != checkpoint_sha256:
        raise RuntimeError("Downloaded checkpoint does not match the frozen SHA-256")

    # Historical Releases copied src itself. Add only that downloaded Artifact path.
    # Future Releases put ray_cats_dogs directly under code and use the default loader.
    legacy_code = model_path / "code" / "src"
    legacy = (legacy_code / "ray_cats_dogs" / "__init__.py").is_file()
    if legacy:
        sys.path.insert(0, str(legacy_code))
    model = mlflow.pytorch.load_model(str(model_path), map_location="cpu")
    import ray_cats_dogs

    if not Path(ray_cats_dogs.__file__).resolve().is_relative_to(model_path.resolve()):
        raise RuntimeError("Model package was resolved outside the downloaded Artifact")
    state = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    expected, actual = state["model_state_dict"], model.state_dict()
    if set(actual) != set(expected) or not all(
        torch.equal(actual[key], expected[key]) for key in expected
    ):
        raise RuntimeError("MLflow model weights differ from the frozen checkpoint")
    model.eval()
    torch.set_num_threads(2)
    with torch.inference_mode():
        prediction = model(torch.zeros(1, 3, *state["image_size"]))
    if tuple(prediction.shape) != (1, 2) or not torch.isfinite(prediction).all():
        raise RuntimeError("Recovered model failed the forward-only CPU fixture")
    result = {
        "run_id": run_id,
        "model_uri": f"runs:/{run_id}/model",
        "checkpoint_sha256": actual_sha,
        "model_path": str(model_path),
        "package_path": ray_cats_dogs.__file__,
        "loader_mode": "artifact-code-src-compatibility" if legacy else "default",
        "default_loader_verified": not legacy,
        "matching_weight_tensors": len(expected),
        "forward_shape": list(prediction.shape),
        "device": "cpu",
        "test_data_read": False,
    }
    (output_dir / "load-verification.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return model, result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracking-uri", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--checkpoint-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    _, verification = load_run_model(**vars(args))
    print(json.dumps(verification, indent=2))


if __name__ == "__main__":
    main()
