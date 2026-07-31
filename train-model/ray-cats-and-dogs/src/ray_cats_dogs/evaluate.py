"""Final PyTorch holdout evaluation for a clean champion role."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def evaluate_checkpoint(
    checkpoint: Any,
    test_records: list[dict[str, Any]],
    pet_images_root: str,
    image_size: tuple[int, int],
    batch_size: int,
) -> dict[str, Any]:
    import numpy as np
    import pandas as pd
    import torch
    from sklearn.metrics import (
        classification_report,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    from ray_cats_dogs.data import CLASS_NAMES
    from ray_cats_dogs.input_pipeline import make_local_dataset
    from ray_cats_dogs.models import build_model

    frame = pd.DataFrame(test_records)
    dataset = make_local_dataset(
        frame,
        Path(pet_images_root),
        image_size=tuple(image_size),
        batch_size=batch_size,
    )
    if torch.cuda.is_available() and not (torch.version.cuda or "").startswith("13."):
        raise RuntimeError(
            "Evaluation worker loaded a non-CUDA-13 PyTorch build; install the "
            "project torch==2.11.0 CUDA 13 environment before evaluation."
        )
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    with checkpoint.as_directory() as checkpoint_directory:
        state = torch.load(
            Path(checkpoint_directory) / "best-model.pt",
            map_location="cpu",
            weights_only=False,
        )
        model = build_model(
            state["model_config"],
            state["training_config"],
            tuple(state["image_size"]),
            int(state["seed"]),
        )
        model.load_state_dict(state["model_state_dict"])
        model.to(device).eval()

    criterion = torch.nn.CrossEntropyLoss()
    labels: list[int] = []
    predictions: list[int] = []
    probabilities: list[np.ndarray] = []
    total_loss = 0.0
    total_count = 0
    with torch.no_grad():
        for images, batch_labels in dataset:
            images = images.to(device, non_blocking=True)
            batch_labels = batch_labels.to(device, non_blocking=True)
            logits = model(images)
            loss = criterion(logits, batch_labels)
            probs = torch.softmax(logits, dim=1)
            total_loss += float(loss.item()) * len(batch_labels)
            total_count += len(batch_labels)
            labels.extend(batch_labels.cpu().tolist())
            predictions.extend(probs.argmax(dim=1).cpu().tolist())
            probabilities.extend(probs.cpu().numpy())

    labels_array = np.asarray(labels, dtype="int64")
    predictions_array = np.asarray(predictions, dtype="int64")
    probability_array = np.asarray(probabilities, dtype="float32")
    metrics = {
        "test_loss": total_loss / max(1, total_count),
        "test_accuracy": float((labels_array == predictions_array).mean()),
        "test_precision": float(
            precision_score(labels_array, predictions_array, zero_division=0)
        ),
        "test_recall": float(
            recall_score(labels_array, predictions_array, zero_division=0)
        ),
        "test_f1": float(
            f1_score(labels_array, predictions_array, zero_division=0)
        ),
        "test_roc_auc": float(
            roc_auc_score(labels_array, probability_array[:, 1])
        ),
    }
    prediction_rows = [
        {
            "relative_path": relative_path,
            "actual_label": int(actual),
            "actual_class": CLASS_NAMES[int(actual)],
            "predicted_label": int(predicted),
            "predicted_class": CLASS_NAMES[int(predicted)],
            "probability_cat": float(probability[0]),
            "probability_dog": float(probability[1]),
        }
        for relative_path, actual, predicted, probability in zip(
            frame["relative_path"],
            labels_array,
            predictions_array,
            probability_array,
            strict=True,
        )
    ]
    return {
        "metrics": metrics,
        "classification_report": classification_report(
            labels_array,
            predictions_array,
            target_names=CLASS_NAMES,
            output_dict=True,
            zero_division=0,
        ),
        "confusion_matrix": confusion_matrix(
            labels_array, predictions_array
        ).tolist(),
        "predictions": prediction_rows,
    }
