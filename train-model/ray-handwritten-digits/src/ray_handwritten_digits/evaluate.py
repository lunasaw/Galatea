"""Final PyTorch holdout evaluation for a clean champion role."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def evaluate_checkpoint(
    checkpoint: Any,
    test_records: list[dict[str, Any]],
    dataset_root: str,
    image_size: tuple[int, int],
    batch_size: int,
) -> dict[str, Any]:
    import numpy as np
    import pandas as pd
    import torch
    from sklearn.metrics import classification_report, confusion_matrix

    from ray_handwritten_digits.data import CLASS_NAMES
    from ray_handwritten_digits.input_pipeline import make_local_dataset
    from ray_handwritten_digits.models import build_model

    frame = pd.DataFrame(test_records)
    dataset = make_local_dataset(
        frame,
        Path(dataset_root),
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
            images = 1.0 - images.to(device, dtype=torch.float32, non_blocking=True)
            batch_labels = batch_labels.to(device, dtype=torch.long, non_blocking=True)
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
    if total_count == 0 or probability_array.shape[1] != len(CLASS_NAMES):
        raise RuntimeError("测试评估收到空数据或错误的十分类输出")
    report = classification_report(
        labels_array,
        predictions_array,
        labels=list(range(len(CLASS_NAMES))),
        target_names=CLASS_NAMES,
        output_dict=True,
        zero_division=0,
    )
    metrics = {
        "test_loss": total_loss / total_count,
        "test_accuracy": float((labels_array == predictions_array).mean()),
        "test_macro_precision": float(report["macro avg"]["precision"]),
        "test_macro_recall": float(report["macro avg"]["recall"]),
        "test_macro_f1": float(report["macro avg"]["f1-score"]),
    }
    prediction_rows = [
        {
            "relative_path": relative_path,
            "actual_label": int(actual),
            "actual_class": CLASS_NAMES[int(actual)],
            "predicted_label": int(predicted),
            "predicted_class": CLASS_NAMES[int(predicted)],
            "probabilities": [float(value) for value in probability],
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
        "classification_report": report,
        "confusion_matrix": confusion_matrix(
            labels_array, predictions_array, labels=list(range(len(CLASS_NAMES)))).tolist(),
        "predictions": prediction_rows,
    }
