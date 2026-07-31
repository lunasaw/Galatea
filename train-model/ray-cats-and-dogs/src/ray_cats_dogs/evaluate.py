"""Final holdout evaluation, executed only for a clean champion role."""

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
    import tensorflow as tf
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

    frame = pd.DataFrame(test_records)
    dataset = make_local_dataset(
        frame,
        Path(pet_images_root),
        image_size=tuple(image_size),
        batch_size=batch_size,
    )
    with checkpoint.as_directory() as checkpoint_directory:
        model = tf.keras.models.load_model(
            Path(checkpoint_directory) / "best-model.keras"
        )
        evaluation = model.evaluate(dataset, verbose=0, return_dict=True)
        probabilities = model.predict(dataset, verbose=0)

    labels = frame["label"].to_numpy(dtype="int64")
    predictions = np.argmax(probabilities, axis=1).astype("int64")
    metrics = {
        "test_loss": float(evaluation["loss"]),
        "test_accuracy": float(evaluation["accuracy"]),
        "test_precision": float(precision_score(labels, predictions, zero_division=0)),
        "test_recall": float(recall_score(labels, predictions, zero_division=0)),
        "test_f1": float(f1_score(labels, predictions, zero_division=0)),
        "test_roc_auc": float(roc_auc_score(labels, probabilities[:, 1])),
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
            labels,
            predictions,
            probabilities,
            strict=True,
        )
    ]
    return {
        "metrics": metrics,
        "classification_report": classification_report(
            labels,
            predictions,
            target_names=CLASS_NAMES,
            output_dict=True,
            zero_division=0,
        ),
        "confusion_matrix": confusion_matrix(labels, predictions).tolist(),
        "predictions": prediction_rows,
    }
