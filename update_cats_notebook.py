from pathlib import Path

import nbformat


NOTEBOOK_PATH = Path("train-model/cats-and-dogs/cats-vs-dogs-classification.ipynb")


def markdown(source):
    return nbformat.v4.new_markdown_cell(source.strip() + "\n")


def code(source):
    return nbformat.v4.new_code_cell(source.strip() + "\n")


notebook = nbformat.read(NOTEBOOK_PATH, as_version=4)
cells = notebook.cells

cells[0].source = """# Cats vs Dogs Classification

This notebook trains and compares two TensorFlow/Keras classifiers. Every training variant is an
independent MLflow Run so its data lineage, parameters, epoch metrics, system telemetry, evaluation
outputs, checkpoint, and deployable model remain auditable.

## Workflow

1. Configure and validate MLflow tracking with a remote MinIO-backed Artifact Store.
2. validate the source images and create a content-addressed data manifest.
3. Create deterministic train, validation, and test splits.
4. Train and evaluate the baseline CNN in a tracked Run.
5. Train and evaluate the augmented CNN in a second tracked Run.
6. Compare both Runs under the same `run_group_id`.

MLflow Server proxies Artifact traffic to MinIO. This notebook must not contain MinIO access keys.
"""

cells[1].source = """<a id="1"></a>

# Imports and reproducible configuration
"""

cells[2].source = """import hashlib
import json
import os
import platform
import random
import shutil
import socket
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import mlflow
import mlflow.data
import mlflow.keras
import numpy as np
import pandas as pd
import tensorflow as tf

from mlflow import MlflowClient
from mlflow.exceptions import MlflowException
from mlflow.models import infer_signature
from PIL import Image, UnidentifiedImageError
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from tensorflow.keras.layers import Dense
from tensorflow.keras.models import Model
from tensorflow.keras.preprocessing.image import ImageDataGenerator

SEED = 42
EPOCHS = int(os.getenv("CATS_DOGS_EPOCHS", "1"))
IMAGE_SIZE = (150, 150)
BASELINE_BATCH_SIZE = 64
AUGMENTED_BATCH_SIZE = 32
LEARNING_RATE = 0.001
TRAIN_FRACTION = 0.90
INCLUDE_TEST = True
EARLY_STOPPING_PATIENCE = 3
MIN_TEST_ACCURACY = float(os.getenv("CATS_DOGS_MIN_TEST_ACCURACY", "0.80"))

REPO_ROOT = Path(
    os.getenv("TRAIN_REPO_ROOT", "/data/ai/chenzhangyue/code/train")
).resolve()
NOTEBOOK_PATH = (
    REPO_ROOT / "train-model/cats-and-dogs/cats-vs-dogs-classification.ipynb"
)
DATA_DIR = Path(
    os.getenv("CATS_DOGS_DATA_DIR", "/data/ai/chenzhangyue/code/data/cats-and-dogs")
).resolve()
PET_IMAGES_DIR = DATA_DIR / "PetImages"
CAT_DIR = PET_IMAGES_DIR / "Cat"
DOG_DIR = PET_IMAGES_DIR / "Dog"
DATASET_SOURCE_URI = os.getenv("CATS_DOGS_DATASET_SOURCE_URI", PET_IMAGES_DIR.as_uri())

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
MLFLOW_EXPERIMENT_NAME = os.getenv(
    "MLFLOW_EXPERIMENT_NAME", "cats-vs-dogs-enterprise"
)
REQUIRE_REMOTE_ARTIFACT_STORE = os.getenv(
    "MLFLOW_REQUIRE_REMOTE_ARTIFACT_STORE", "true"
).lower() == "true"
RUN_GROUP_ID = os.getenv(
    "MLFLOW_RUN_GROUP_ID",
    f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}",
)

random.seed(SEED)
np.random.seed(SEED)
tf.random.set_seed(SEED)
"""

cells[3].source = """<a id="2"></a>

# Data validation and deterministic splitting
"""

cells[4].source = """The tracking preflight fails closed when MLflow is unavailable or the Experiment is not configured
with the MinIO-backed remote Artifact Store. A formal training run must not continue untracked.
"""

cells[5].source = """if not CAT_DIR.is_dir() or not DOG_DIR.is_dir():
    raise FileNotFoundError(
        f"Extract the dataset first so that {CAT_DIR} and {DOG_DIR} exist. "
        "See train-model/cats-and-dogs/README.md for setup instructions."
    )

class_names = ["Cat", "Dog"]
image_suffixes = {".jpg", ".jpeg", ".png"}
n_cats = sum(path.suffix.lower() in image_suffixes for path in CAT_DIR.iterdir())
n_dogs = sum(path.suffix.lower() in image_suffixes for path in DOG_DIR.iterdir())
if (n_cats, n_dogs) != (12500, 12500):
    raise RuntimeError(
        f"Incomplete extraction: found {n_cats} cat and {n_dogs} dog images"
    )

figure, axis = plt.subplots(figsize=(5, 5))
axis.pie(
    [n_cats, n_dogs],
    labels=class_names,
    autopct="%1.1f%%",
    colors=["#fad25a", "#e4572e"],
)
axis.set_title(f"Dataset distribution ({n_cats + n_dogs:,} image files)")
plt.show()
"""

cells[6].source = """The source contains 12,500 image files per class. The validation step below records and excludes
unreadable files before computing the immutable dataset identity.
"""

cells[7].source = """<a name="2-1"></a>

## Create isolated train, validation, and test directories
"""

cells[8].source = """SPLIT_ROOT = Path("/tmp/cats-v-dogs")
if SPLIT_ROOT.resolve() != Path("/tmp/cats-v-dogs"):
    raise RuntimeError(f"Refusing to clean unexpected split path: {SPLIT_ROOT}")

shutil.rmtree(SPLIT_ROOT, ignore_errors=True)
for split_name in ("training", "validation", "test"):
    for class_name in ("cats", "dogs"):
        (SPLIT_ROOT / split_name / class_name).mkdir(parents=True, exist_ok=True)
"""

cells[9].source = """TRAINING_DIR = SPLIT_ROOT / "training"
VALIDATION_DIR = SPLIT_ROOT / "validation"
TEST_DIR = SPLIT_ROOT / "test"

SPLIT_DIRECTORIES = {
    "training": TRAINING_DIR,
    "validation": VALIDATION_DIR,
    "test": TEST_DIR,
}
"""

cells[10].source = """pd.DataFrame(
    [
        {"split": split_name, "local_cache": str(split_path)}
        for split_name, split_path in SPLIT_DIRECTORIES.items()
    ]
)
"""

cells[11].source = """## Validate images, assign deterministic splits, and build a manifest

The manifest stores relative paths, labels, byte sizes, and SHA-256 checksums. Raw images are not
copied into MLflow artifacts. The manifest provides traceability without duplicating the dataset.
"""

cells[12].source = """def file_sha256(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def split_data(main_dir, output_class_name, label, split_size=TRAIN_FRACTION):
    if not 0 < split_size < 1:
        raise ValueError("split_size must be between 0 and 1")

    valid_files = []
    invalid_files = []
    for path in sorted(Path(main_dir).iterdir()):
        if path.suffix.lower() not in image_suffixes or path.stat().st_size == 0:
            continue
        try:
            with Image.open(path) as image:
                image.verify()
            valid_files.append(path)
        except (OSError, UnidentifiedImageError) as error:
            invalid_files.append(
                {
                    "relative_path": path.relative_to(PET_IMAGES_DIR).as_posix(),
                    "error_type": type(error).__name__,
                }
            )

    random.Random(SEED + label).shuffle(valid_files)
    train_end = int(split_size * len(valid_files))
    validation_end = train_end + (len(valid_files) - train_end) // 2
    split_files = {
        "training": valid_files[:train_end],
        "validation": valid_files[train_end:validation_end],
        "test": valid_files[validation_end:],
    }

    records = []
    for split_name, paths in split_files.items():
        destination = SPLIT_DIRECTORIES[split_name] / output_class_name
        for source_path in paths:
            shutil.copy2(source_path, destination / source_path.name)
            records.append(
                {
                    "relative_path": source_path.relative_to(PET_IMAGES_DIR).as_posix(),
                    "split": split_name,
                    "class_name": class_names[label],
                    "label": label,
                    "bytes": source_path.stat().st_size,
                    "sha256": file_sha256(source_path),
                }
            )

    print(
        f"{class_names[label]}: "
        + ", ".join(f"{name}={len(paths)}" for name, paths in split_files.items())
        + f", invalid={len(invalid_files)}"
    )
    return records, invalid_files
"""

cells[13].source = """The same seed and the same validated file set always produce the same split assignment.
"""

cells[14].source = """cat_records, invalid_cat_files = split_data(CAT_DIR, "cats", label=0)
dog_records, invalid_dog_files = split_data(DOG_DIR, "dogs", label=1)

manifest = pd.DataFrame(cat_records + dog_records).sort_values(
    ["relative_path", "split"]
).reset_index(drop=True)
invalid_files = invalid_cat_files + invalid_dog_files

content_hasher = hashlib.sha256()
split_hasher = hashlib.sha256()
for record in manifest.to_dict(orient="records"):
    content_hasher.update(
        f"{record['relative_path']}|{record['bytes']}|{record['sha256']}\n".encode()
    )
    split_hasher.update(
        f"{record['relative_path']}|{record['split']}\n".encode()
    )

DATASET_CONTENT_DIGEST = content_hasher.hexdigest()
SPLIT_DIGEST = split_hasher.hexdigest()
DATASET_VERSION = os.getenv(
    "CATS_DOGS_DATASET_VERSION", f"sha256-{DATASET_CONTENT_DIGEST[:16]}"
)
MANIFEST_PATH = SPLIT_ROOT / "dataset-manifest.csv"
manifest.to_csv(MANIFEST_PATH, index=False)

split_counts = manifest.groupby(["split", "class_name"]).size().unstack(fill_value=0)
DATASET_PROFILE = {
    "dataset_name": "microsoft-cats-vs-dogs",
    "dataset_version": DATASET_VERSION,
    "source_uri": DATASET_SOURCE_URI,
    "content_sha256": DATASET_CONTENT_DIGEST,
    "split_sha256": SPLIT_DIGEST,
    "valid_images": int(len(manifest)),
    "invalid_images": invalid_files,
    "total_bytes": int(manifest["bytes"].sum()),
    "split_counts": {
        split_name: {
            class_name: int(count)
            for class_name, count in class_counts.items()
        }
        for split_name, class_counts in split_counts.to_dict(orient="index").items()
    },
}
"""

cells[15].source = """## Data quality gates
"""

cells[16].source = """if manifest["relative_path"].duplicated().any():
    raise RuntimeError("The split manifest contains duplicate source images")
if set(manifest["split"]) != {"training", "validation", "test"}:
    raise RuntimeError("The manifest is missing a required split")
if not all(split_counts.get(class_name, pd.Series(dtype=int)).gt(0).all() for class_name in class_names):
    raise RuntimeError("At least one split has an empty class")
if len(manifest) != 24998:
    raise RuntimeError(
        f"Expected 24,998 valid images after excluding known corrupt files; found {len(manifest)}"
    )

print(f"Dataset version: {DATASET_VERSION}")
print(f"Content SHA-256: {DATASET_CONTENT_DIGEST}")
display(split_counts)
"""

cells[17].source = """<a name="2-2"></a>

## Create Keras generators
"""

cells[18].source = """train_gen = ImageDataGenerator(rescale=1.0 / 255)
validation_gen = ImageDataGenerator(rescale=1.0 / 255)
test_gen = ImageDataGenerator(rescale=1.0 / 255)
"""

cells[19].source = """train_generator = train_gen.flow_from_directory(
    str(TRAINING_DIR),
    target_size=IMAGE_SIZE,
    batch_size=BASELINE_BATCH_SIZE,
    class_mode="binary",
    seed=SEED,
)
validation_generator = validation_gen.flow_from_directory(
    str(VALIDATION_DIR),
    target_size=IMAGE_SIZE,
    batch_size=BASELINE_BATCH_SIZE,
    class_mode="binary",
    shuffle=False,
)
test_generator = test_gen.flow_from_directory(
    str(TEST_DIR),
    target_size=IMAGE_SIZE,
    batch_size=BASELINE_BATCH_SIZE,
    class_mode="binary",
    shuffle=False,
)
"""

cells[20].source = """Preview a sample from each split. The generator is reset after sampling so the preview does not
change training or evaluation order.
"""

cells[21].source = """def plot_data(generator, n_images):
    generator.reset()
    images, labels = next(generator)
    generator.reset()
    labels = labels.astype("int32")

    n_images = min(n_images, len(images))
    rows = int(np.ceil(n_images / 3))
    figure = plt.figure(figsize=(12, 4 * rows))
    for index, (image, label) in enumerate(
        zip(images[:n_images], labels[:n_images]), start=1
    ):
        plt.subplot(rows, 3, index)
        plt.imshow(image)
        plt.title(class_names[label])
        plt.axis("off")

    plt.tight_layout()
    plt.show()
    return figure
"""

cells[22].source = "plot_data(train_generator, 7)"
cells[23].source = "plot_data(validation_generator, 7)"
cells[24].source = "plot_data(test_generator, 10)"

cells[25].source = """The data is now ready for reproducible, tracked model training.
"""

cells[26].source = """<a id="3"></a>

# Baseline model
"""

cells[27].source = """inputs = tf.keras.layers.Input(shape=(*IMAGE_SIZE, 3))
x = tf.keras.layers.Conv2D(32, (3, 3), activation="relu")(inputs)
x = tf.keras.layers.Conv2D(64, (3, 3), activation="relu")(x)
x = tf.keras.layers.MaxPooling2D(2, 2)(x)
x = tf.keras.layers.Conv2D(64, (3, 3), activation="relu")(x)
x = tf.keras.layers.Conv2D(128, (3, 3), activation="relu")(x)
x = tf.keras.layers.MaxPooling2D(2, 2)(x)
x = tf.keras.layers.Conv2D(128, (3, 3), activation="relu")(x)
x = tf.keras.layers.Conv2D(256, (3, 3), activation="relu")(x)
x = tf.keras.layers.GlobalAveragePooling2D()(x)
x = Dense(1024, activation="relu")(x)
outputs = tf.keras.layers.Dense(2, activation="softmax")(x)

model = Model(inputs=inputs, outputs=outputs, name="cats_dogs_baseline_cnn")
"""

cells[28].source = """model.compile(
    optimizer=tf.keras.optimizers.RMSprop(learning_rate=LEARNING_RATE),
    loss="sparse_categorical_crossentropy",
    metrics=["accuracy"],
)
"""

cells[29].source = """baseline_result = run_tracked_training(
    variant="baseline",
    model=model,
    train_data=train_generator,
    validation_data=validation_generator,
    test_data=test_generator,
    batch_size=BASELINE_BATCH_SIZE,
    augmentation_config={"enabled": False},
)
model = baseline_result["model"]
r = baseline_result["history"]

print(f"Baseline MLflow Run ID: {baseline_result['run_id']}")
print(f"Model URI: {baseline_result['model_uri']}")
pd.Series(baseline_result["test_metrics"], name="value")
"""

cells[30].source = """<a id="4"></a>

# Baseline evaluation and explainability
"""

cells[31].source = """<a name="4-1"></a>

## Fixed test-set metrics

The authoritative values are logged under `test_*` in the baseline MLflow Run.
"""

cells[32].source = "pd.Series(baseline_result['test_metrics'], name='value')"

cells[33].source = """<a name="4-2"></a>

## Visualize predictions
"""

cells[34].source = """def plot_prediction(trained_model, generator, n_images):
    generator.reset()
    images, labels = next(generator)
    generator.reset()
    probabilities = trained_model.predict(images, verbose=0)
    predictions = np.argmax(probabilities, axis=1)
    labels = labels.astype("int32")

    n_images = min(n_images, len(images))
    rows = int(np.ceil(n_images / 3))
    plt.figure(figsize=(12, 4 * rows))
    for index, (image, label) in enumerate(zip(images[:n_images], labels[:n_images])):
        plt.subplot(rows, 3, index + 1)
        plt.imshow(image)
        color = "green" if predictions[index] == label else "red"
        plt.title(
            f"Actual: {class_names[label]} | "
            f"Predicted: {class_names[predictions[index]]}",
            color=color,
        )
        plt.axis("off")

    plt.tight_layout()
    plt.show()
"""

cells[35].source = "plot_prediction(model, test_generator, 10)"
cells[36].source = "plot_prediction(model, validation_generator, 10)"

cells[37].source = """<a name="4-3"></a>

## Visualize class activation maps
"""

cells[38].source = """last_conv_layer = next(
    layer for layer in reversed(model.layers)
    if isinstance(layer, tf.keras.layers.Conv2D)
)
activation_model = Model(model.inputs, [last_conv_layer.output, model.output])
"""

cells[39].source = """test_generator.reset()
images, _ = next(test_generator)
test_generator.reset()
gradcam_predictions = model.predict(images, verbose=0)
"""

cells[40].source = """def make_gradcam_heatmap(image):
    image_batch = tf.convert_to_tensor(image[None, ...], dtype=tf.float32)
    with tf.GradientTape() as tape:
        conv_output, predictions = activation_model(image_batch, training=False)
        prediction = tf.argmax(predictions[0])
        class_score = predictions[:, prediction]

    gradients = tape.gradient(class_score, conv_output)
    pooled_gradients = tf.reduce_mean(gradients, axis=(0, 1, 2))
    heatmap = tf.reduce_sum(conv_output[0] * pooled_gradients, axis=-1)
    heatmap = tf.maximum(heatmap, 0)
    heatmap /= tf.reduce_max(heatmap) + tf.keras.backend.epsilon()
    return heatmap.numpy(), int(prediction), float(predictions[0, prediction])


def show_cam(image_index):
    heatmap, prediction, probability = make_gradcam_heatmap(images[image_index])
    heatmap = tf.image.resize(heatmap[..., None], IMAGE_SIZE).numpy().squeeze()

    print(
        f"Predicted Class = {class_names[prediction]}, "
        f"Probability = {probability:.3f}"
    )
    plt.imshow(images[image_index])
    plt.imshow(heatmap, cmap="jet", alpha=0.45)
    plt.axis("off")
    plt.show()
"""

cells[41].source = """def show_maps(desired_class, num_maps):
    matching_indices = np.flatnonzero(
        np.argmax(gradcam_predictions, axis=1) == desired_class
    )
    for image_index in matching_indices[:num_maps]:
        show_cam(image_index)
    if len(matching_indices) == 0:
        print(f"No {class_names[desired_class]} predictions in this batch.")
"""

cells[42].source = "show_maps(desired_class=1, num_maps=5)"
cells[43].source = "show_maps(desired_class=0, num_maps=5)"

cells[44].source = """<a name="4-4"></a>

## Visualize baseline training history
"""

cells[45].source = """baseline_history = pd.DataFrame(r.history)
baseline_history.tail()
"""

cells[46].source = """axis = baseline_history[["accuracy", "val_accuracy"]].plot(
    figsize=(8, 4), color=["#d4a600", "#e4572e"], marker="o"
)
axis.set(
    title="Training and validation accuracy",
    xlabel="Epoch",
    ylabel="Accuracy",
)
axis.grid(alpha=0.25)
plt.show()
"""

cells[47].source = """axis = baseline_history[["loss", "val_loss"]].plot(
    figsize=(8, 4), color=["#d4a600", "#e4572e"], marker="o"
)
axis.set(title="Training and validation loss", xlabel="Epoch", ylabel="Loss")
axis.grid(alpha=0.25)
plt.show()
"""

cells[48].source = """The next Run uses the same immutable dataset version and model family, changing only the declared
augmentation policy, hidden-layer width, and batch size.
"""

cells[49].source = """<a id="5"></a>

# Data-augmented model
"""

cells[50].source = """train_gen_aug = ImageDataGenerator(
    rescale=1.0 / 255,
    fill_mode="nearest",
    horizontal_flip=True,
    rotation_range=20,
    width_shift_range=0.2,
    height_shift_range=0.2,
)
validation_gen_aug = ImageDataGenerator(rescale=1.0 / 255)
"""

cells[51].source = """train_generator_aug = train_gen_aug.flow_from_directory(
    str(TRAINING_DIR),
    target_size=IMAGE_SIZE,
    batch_size=AUGMENTED_BATCH_SIZE,
    class_mode="binary",
    seed=SEED,
)
validation_generator_aug = validation_gen_aug.flow_from_directory(
    str(VALIDATION_DIR),
    target_size=IMAGE_SIZE,
    batch_size=AUGMENTED_BATCH_SIZE,
    class_mode="binary",
    shuffle=False,
)
"""

cells[52].source = """inputs = tf.keras.layers.Input(shape=(*IMAGE_SIZE, 3))
x = tf.keras.layers.Conv2D(32, (3, 3), activation="relu")(inputs)
x = tf.keras.layers.Conv2D(64, (3, 3), activation="relu")(x)
x = tf.keras.layers.MaxPooling2D(2, 2)(x)
x = tf.keras.layers.Conv2D(64, (3, 3), activation="relu")(x)
x = tf.keras.layers.Conv2D(128, (3, 3), activation="relu")(x)
x = tf.keras.layers.MaxPooling2D(2, 2)(x)
x = tf.keras.layers.Conv2D(128, (3, 3), activation="relu")(x)
x = tf.keras.layers.Conv2D(256, (3, 3), activation="relu")(x)
x = tf.keras.layers.MaxPooling2D(2, 2)(x)
x = tf.keras.layers.GlobalAveragePooling2D()(x)
x = tf.keras.layers.Dense(256, activation="relu")(x)
outputs = tf.keras.layers.Dense(2, activation="softmax")(x)

model_aug = Model(inputs=inputs, outputs=outputs, name="cats_dogs_augmented_cnn")
"""

cells[53].source = """model_aug.compile(
    optimizer=tf.keras.optimizers.RMSprop(learning_rate=LEARNING_RATE),
    loss="sparse_categorical_crossentropy",
    metrics=["accuracy"],
)
"""

cells[54].source = """augmented_result = run_tracked_training(
    variant="augmented",
    model=model_aug,
    train_data=train_generator_aug,
    validation_data=validation_generator_aug,
    test_data=test_generator,
    batch_size=AUGMENTED_BATCH_SIZE,
    augmentation_config={
        "enabled": True,
        "horizontal_flip": True,
        "rotation_range": 20,
        "width_shift_range": 0.2,
        "height_shift_range": 0.2,
        "fill_mode": "nearest",
    },
)
model_aug = augmented_result["model"]
r_aug = augmented_result["history"]

print(f"Augmented MLflow Run ID: {augmented_result['run_id']}")
print(f"Model URI: {augmented_result['model_uri']}")
pd.Series(augmented_result["test_metrics"], name="value")
"""

cells[55].source = """augmented_history = pd.DataFrame(r_aug.history)
augmented_history.tail()
"""

cells[56].source = """axis = augmented_history[["accuracy", "val_accuracy"]].plot(
    figsize=(8, 4), color=["#d4a600", "#e4572e"], marker="o"
)
axis.set(
    title="Augmented training and validation accuracy",
    xlabel="Epoch",
    ylabel="Accuracy",
)
axis.grid(alpha=0.25)
plt.show()
"""

cells[57].source = """axis = augmented_history[["loss", "val_loss"]].plot(
    figsize=(8, 4), color=["#d4a600", "#e4572e"], marker="o"
)
axis.set(
    title="Augmented training and validation loss",
    xlabel="Epoch",
    ylabel="Loss",
)
axis.grid(alpha=0.25)
plt.show()
"""

cells[58].source = """<a id="6"></a>

# Tracked comparison and conclusion
"""

cells[59].source = """The result is determined from fixed test-set metrics, not from an assumption that augmentation must
help. Use the Run IDs and model URIs below for review, reproducibility, and promotion workflows.
"""

cells[60].source = """# End of notebook. Model promotion is intentionally kept outside exploratory training.
"""

tracking_setup_markdown = markdown(
    """## MLflow and MinIO tracking preflight

The MLflow Tracking Server owns MinIO credentials and proxies all Artifact operations. The client
only receives the tracking URI. The preflight also captures Git state and rejects a local Artifact
Store by default.
"""
)

tracking_setup_code = code(
    """def command_output(arguments):
    result = subprocess.run(
        arguments,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


GIT_COMMIT = command_output(["git", "rev-parse", "HEAD"]) or "uncommitted"
GIT_DIRTY = bool(command_output(["git", "status", "--porcelain"]))

mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
tracking_client = MlflowClient()
try:
    tracking_client.search_experiments(max_results=1)
    experiment = mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)
except MlflowException as error:
    raise ConnectionError(
        f"MLflow is unavailable at {MLFLOW_TRACKING_URI}. Start mlflow.service "
        "and verify /health before formal training."
    ) from error

artifact_scheme = experiment.artifact_location.split(":", maxsplit=1)[0]
if REQUIRE_REMOTE_ARTIFACT_STORE and artifact_scheme not in {"mlflow-artifacts", "s3"}:
    raise RuntimeError(
        "Experiment Artifact Store is not remote: "
        f"{experiment.artifact_location}. Expected the MLflow proxy or s3:// MinIO."
    )

print(f"MLflow version: {mlflow.__version__}")
print(f"Tracking URI: {mlflow.get_tracking_uri()}")
print(f"Experiment: {experiment.name} ({experiment.experiment_id})")
print(f"Artifact location: {experiment.artifact_location}")
print(f"Run group: {RUN_GROUP_ID}")
"""
)

tracking_contract_markdown = markdown(
    """## Enterprise tracking contract

Each variant is one MLflow Run. Dataset manifests are logged as Run inputs; epoch and system
telemetry are time-series metrics; predictions, reports, plots, checkpoints, and the signed Keras
model are output artifacts stored through MLflow in MinIO. Artifact round-trip verification is
required before a Run is marked successful.
"""
)

tracking_contract_code = code(
    """class EpochTelemetryCallback(tf.keras.callbacks.Callback):
    def on_epoch_begin(self, epoch, logs=None):
        self.epoch_started_at = time.perf_counter()

    def on_epoch_end(self, epoch, logs=None):
        logs = logs or {}
        metric_names = {
            "loss": "train_loss",
            "accuracy": "train_accuracy",
            "val_loss": "val_loss",
            "val_accuracy": "val_accuracy",
        }
        metrics = {
            metric_names[name]: float(value)
            for name, value in logs.items()
            if name in metric_names and np.isfinite(value)
        }
        metrics["epoch_duration_seconds"] = time.perf_counter() - self.epoch_started_at
        metrics["learning_rate"] = float(
            tf.keras.backend.get_value(self.model.optimizer.learning_rate)
        )
        mlflow.log_metrics(metrics, step=epoch)


def split_digest(split_frame):
    payload = split_frame[
        ["relative_path", "label", "bytes", "sha256"]
    ].to_csv(index=False).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def log_dataset_inputs():
    dataset_columns = ["relative_path", "class_name", "label", "bytes", "sha256"]
    for split_name in ("training", "validation", "test"):
        split_frame = manifest.loc[manifest["split"] == split_name, dataset_columns].copy()
        dataset = mlflow.data.from_pandas(
            split_frame,
            source=DATASET_SOURCE_URI,
            targets="label",
            name=f"microsoft-cats-vs-dogs-{split_name}",
            digest=split_digest(split_frame),
        )
        mlflow.log_input(dataset, context=split_name)


def evaluate_classifier(trained_model, generator):
    generator.reset()
    evaluation = trained_model.evaluate(generator, verbose=0, return_dict=True)
    generator.reset()
    probabilities = trained_model.predict(generator, verbose=0)
    generator.reset()

    labels = generator.classes.astype("int32")
    predictions = np.argmax(probabilities, axis=1).astype("int32")
    metrics = {
        "test_loss": float(evaluation["loss"]),
        "test_accuracy": float(evaluation["accuracy"]),
        "test_precision": float(precision_score(labels, predictions, zero_division=0)),
        "test_recall": float(recall_score(labels, predictions, zero_division=0)),
        "test_f1": float(f1_score(labels, predictions, zero_division=0)),
        "test_roc_auc": float(roc_auc_score(labels, probabilities[:, 1])),
    }
    report = classification_report(
        labels,
        predictions,
        target_names=class_names,
        output_dict=True,
        zero_division=0,
    )
    predictions_frame = pd.DataFrame(
        {
            "relative_path": [
                Path(path).relative_to(SPLIT_ROOT).as_posix()
                for path in generator.filepaths
            ],
            "actual_label": labels,
            "actual_class": [class_names[label] for label in labels],
            "predicted_label": predictions,
            "predicted_class": [class_names[label] for label in predictions],
            "probability_cat": probabilities[:, 0],
            "probability_dog": probabilities[:, 1],
        }
    )
    return metrics, report, predictions_frame, confusion_matrix(labels, predictions)


def log_training_plots(history_frame, matrix):
    figure, axes = plt.subplots(1, 2, figsize=(13, 4))
    history_frame.plot(
        x="epoch", y=["accuracy", "val_accuracy"], marker="o", ax=axes[0]
    )
    history_frame.plot(x="epoch", y=["loss", "val_loss"], marker="o", ax=axes[1])
    axes[0].set_title("Training and validation accuracy")
    axes[1].set_title("Training and validation loss")
    for axis in axes:
        axis.grid(alpha=0.25)
    figure.tight_layout()
    mlflow.log_figure(figure, "plots/training-curves.png")
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(5, 4))
    image = axis.imshow(matrix, interpolation="nearest", cmap="Blues")
    figure.colorbar(image, ax=axis)
    axis.set(
        title="Test confusion matrix",
        xlabel="Predicted class",
        ylabel="Actual class",
        xticks=range(len(class_names)),
        yticks=range(len(class_names)),
        xticklabels=class_names,
        yticklabels=class_names,
    )
    threshold = matrix.max() / 2
    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            axis.text(
                column,
                row,
                str(matrix[row, column]),
                ha="center",
                va="center",
                color="white" if matrix[row, column] > threshold else "black",
            )
    figure.tight_layout()
    mlflow.log_figure(figure, "plots/test-confusion-matrix.png")
    plt.close(figure)


def environment_report():
    gpu_devices = []
    for device in tf.config.list_physical_devices("GPU"):
        details = tf.config.experimental.get_device_details(device)
        gpu_devices.append(details.get("device_name", device.name))
    return {
        "python": platform.python_version(),
        "tensorflow": tf.__version__,
        "mlflow": mlflow.__version__,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "operating_system": platform.platform(),
        "gpu_devices": gpu_devices,
    }


def run_tracked_training(
    variant,
    model,
    train_data,
    validation_data,
    test_data,
    batch_size,
    augmentation_config,
):
    if mlflow.active_run() is not None:
        raise RuntimeError("Close the active MLflow Run before starting a model variant")

    run_name = f"{RUN_GROUP_ID}-{variant}"
    tags = {
        "project": "cats-vs-dogs",
        "run_group_id": RUN_GROUP_ID,
        "variant": variant,
        "dataset_version": DATASET_VERSION,
        "code.git_commit": GIT_COMMIT,
        "code.git_dirty": str(GIT_DIRTY).lower(),
        "execution.host": socket.gethostname(),
        "execution.type": "notebook",
        "lifecycle.stage": "development",
    }
    parameters = {
        "model.variant": variant,
        "model.parameter_count": model.count_params(),
        "model.input_height": IMAGE_SIZE[0],
        "model.input_width": IMAGE_SIZE[1],
        "model.output_classes": len(class_names),
        "training.epochs_requested": EPOCHS,
        "training.batch_size": batch_size,
        "training.learning_rate": LEARNING_RATE,
        "training.optimizer": type(model.optimizer).__name__,
        "training.loss": "sparse_categorical_crossentropy",
        "training.seed": SEED,
        "training.early_stopping_patience": EARLY_STOPPING_PATIENCE,
        "data.dataset_version": DATASET_VERSION,
        "data.content_sha256": DATASET_CONTENT_DIGEST,
        "data.split_sha256": SPLIT_DIGEST,
        "data.train_fraction": TRAIN_FRACTION,
        "data.training_images": int((manifest["split"] == "training").sum()),
        "data.validation_images": int((manifest["split"] == "validation").sum()),
        "data.test_images": int((manifest["split"] == "test").sum()),
        "quality.min_test_accuracy": MIN_TEST_ACCURACY,
        **{
            f"augmentation.{name}": value
            for name, value in augmentation_config.items()
        },
    }

    with mlflow.start_run(
        run_name=run_name,
        tags=tags,
        description=(
            "TensorFlow cats-vs-dogs classifier with content-addressed data lineage "
            "and MinIO-backed artifacts."
        ),
        log_system_metrics=True,
    ) as active_run:
        run_id = active_run.info.run_id
        checkpoint_dir = SPLIT_ROOT / "checkpoints" / run_id
        checkpoint_dir.mkdir(parents=True, exist_ok=False)
        checkpoint_path = checkpoint_dir / "best.keras"

        try:
            mlflow.log_params(parameters)
            mlflow.log_dict(
                {
                    "parameters": parameters,
                    "augmentation": augmentation_config,
                    "class_names": class_names,
                },
                "config/training-config.json",
            )
            mlflow.log_dict(DATASET_PROFILE, "data/dataset-profile.json")
            mlflow.log_artifact(str(MANIFEST_PATH), artifact_path="data")
            if NOTEBOOK_PATH.is_file():
                mlflow.log_artifact(str(NOTEBOOK_PATH), artifact_path="source")
            mlflow.log_dict(environment_report(), "environment/runtime.json")
            log_dataset_inputs()

            started_at = time.perf_counter()
            history = model.fit(
                train_data,
                epochs=EPOCHS,
                validation_data=validation_data,
                callbacks=[
                    EpochTelemetryCallback(),
                    tf.keras.callbacks.TerminateOnNaN(),
                    tf.keras.callbacks.EarlyStopping(
                        monitor="val_loss",
                        patience=EARLY_STOPPING_PATIENCE,
                        restore_best_weights=True,
                    ),
                    tf.keras.callbacks.ModelCheckpoint(
                        filepath=str(checkpoint_path),
                        monitor="val_loss",
                        mode="min",
                        save_best_only=True,
                    ),
                ],
            )
            training_seconds = time.perf_counter() - started_at

            history_frame = pd.DataFrame(history.history)
            history_frame.insert(0, "epoch", np.arange(1, len(history_frame) + 1))
            best_epoch = int(history_frame["val_loss"].idxmin()) + 1
            test_metrics, report, predictions, matrix = evaluate_classifier(
                model, test_data
            )
            output_digest = hashlib.sha256(
                predictions.to_csv(index=False).encode()
            ).hexdigest()
            quality_passed = test_metrics["test_accuracy"] >= MIN_TEST_ACCURACY

            final_metrics = {
                **test_metrics,
                "training_duration_seconds": training_seconds,
                "epochs_completed": float(len(history_frame)),
                "best_epoch": float(best_epoch),
                "best_val_loss": float(history_frame["val_loss"].min()),
                "best_val_accuracy": float(history_frame["val_accuracy"].max()),
            }
            mlflow.log_metrics(final_metrics)
            mlflow.log_table(history_frame, "metrics/training-history.json")
            mlflow.log_table(predictions, "outputs/test-predictions.json")
            mlflow.log_dict(
                {
                    "run_id": run_id,
                    "dataset_version": DATASET_VERSION,
                    "prediction_sha256": output_digest,
                    "metrics": test_metrics,
                    "classification_report": report,
                    "quality_gate": {
                        "minimum_test_accuracy": MIN_TEST_ACCURACY,
                        "passed": quality_passed,
                    },
                },
                "reports/evaluation.json",
            )
            log_training_plots(history_frame, matrix)
            mlflow.log_artifact(str(checkpoint_path), artifact_path="checkpoints")

            test_data.reset()
            input_batch, _ = next(test_data)
            test_data.reset()
            input_example = input_batch[:4]
            output_example = model.predict(input_example, verbose=0)
            signature = infer_signature(input_example, output_example)
            model_info = mlflow.keras.log_model(
                model,
                name="model",
                signature=signature,
                input_example=input_example,
                metadata={
                    "dataset_version": DATASET_VERSION,
                    "prediction_sha256": output_digest,
                    "class_names": class_names,
                    "input_scaling": "pixel_value / 255.0",
                },
            )

            verification_payload = {
                "run_id": run_id,
                "dataset_version": DATASET_VERSION,
                "artifact_uri": active_run.info.artifact_uri,
            }
            mlflow.log_dict(
                verification_payload, "verification/artifact-round-trip.json"
            )
            verification_dir = checkpoint_dir / "artifact-download"
            verification_dir.mkdir()
            downloaded_path = mlflow.artifacts.download_artifacts(
                run_id=run_id,
                artifact_path="verification/artifact-round-trip.json",
                dst_path=str(verification_dir),
            )
            with Path(downloaded_path).open(encoding="utf-8") as file_handle:
                downloaded_payload = json.load(file_handle)
            if downloaded_payload != verification_payload:
                raise RuntimeError("Artifact round-trip verification returned different content")

            mlflow.set_tags(
                {
                    "run.outcome": "succeeded",
                    "quality_gate.passed": str(quality_passed).lower(),
                    "artifact.roundtrip_verified": "true",
                    "output.prediction_sha256": output_digest,
                    "model.uri": model_info.model_uri,
                }
            )
            mlflow.flush_async_logging()
        except Exception as error:
            mlflow.set_tags(
                {
                    "run.outcome": "failed",
                    "failure.type": type(error).__name__,
                    "failure.message": str(error)[:500],
                }
            )
            raise

    return {
        "run_id": run_id,
        "model_uri": model_info.model_uri,
        "artifact_uri": active_run.info.artifact_uri,
        "model": model,
        "history": history,
        "test_metrics": test_metrics,
        "predictions": predictions,
        "quality_gate_passed": quality_passed,
    }
"""
)

comparison_markdown = markdown(
    """## Compare Runs in MLflow

The query is scoped to this notebook execution's `run_group_id`, so previous experiments cannot be
mixed into the decision table.
"""
)

comparison_code = code(
    """comparison = mlflow.search_runs(
    experiment_ids=[experiment.experiment_id],
    filter_string=f"tags.run_group_id = '{RUN_GROUP_ID}'",
    order_by=["metrics.test_accuracy DESC"],
)
comparison_columns = [
    "run_id",
    "tags.variant",
    "status",
    "metrics.test_accuracy",
    "metrics.test_f1",
    "metrics.test_roc_auc",
    "metrics.best_val_loss",
    "metrics.training_duration_seconds",
    "tags.quality_gate.passed",
    "tags.artifact.roundtrip_verified",
    "tags.model.uri",
]
comparison[[column for column in comparison_columns if column in comparison.columns]]
"""
)

new_cells = []
for index, cell in enumerate(cells):
    new_cells.append(cell)
    if index == 2:
        new_cells.extend([tracking_setup_markdown, tracking_setup_code])
    if index == 24:
        new_cells.extend([tracking_contract_markdown, tracking_contract_code])
    if index == 57:
        new_cells.extend([comparison_markdown, comparison_code])

notebook.cells = new_cells
notebook.nbformat_minor = 5
for cell in notebook.cells:
    if cell.cell_type == "code":
        cell.execution_count = None
        cell.outputs = []

nbformat.validate(notebook)
nbformat.write(notebook, NOTEBOOK_PATH)
