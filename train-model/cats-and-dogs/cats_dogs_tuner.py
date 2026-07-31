"""History-aware MLflow auto-tuning for the cats-vs-dogs pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable

import mlflow
import numpy as np
import tensorflow as tf
from mlflow import MlflowClient
from sklearn.ensemble import RandomForestRegressor
from tensorflow.keras.models import Model
from tensorflow.keras.preprocessing.image import ImageDataGenerator

from cats_dogs_pipeline import (
    CLASS_NAMES,
    DataGenerators,
    PipelineConfig,
    PreparedDataset,
    TrackingContext,
    augmentation_policy,
    configure_tensorflow_runtime,
    prepare_dataset,
    preflight_tracking,
    run_tracked_training,
)


SEARCH_VERSION = "v1"
MODULE_PATH = Path(__file__).resolve()
ARCHITECTURES = (
    "custom_baseline",
    "custom_augmented",
    "mobilenet_v2",
    "efficientnet_b0",
)
OPTIMIZERS = ("adam", "rmsprop")


@dataclass(frozen=True)
class TrialSpec:
    architecture: str
    optimizer: str
    learning_rate: float
    batch_size: int
    dense_units: int
    dropout: float
    augmentation: bool
    trainable_backbone_layers: int = 0

    def __post_init__(self) -> None:
        if self.architecture not in ARCHITECTURES:
            raise ValueError(f"Unsupported architecture: {self.architecture}")
        if self.optimizer not in OPTIMIZERS:
            raise ValueError(f"Unsupported optimizer: {self.optimizer}")
        if self.learning_rate <= 0:
            raise ValueError("learning_rate must be positive")
        if self.batch_size < 1 or self.dense_units < 1:
            raise ValueError("batch_size and dense_units must be positive")
        if not 0 <= self.dropout < 1:
            raise ValueError("dropout must be in [0, 1)")
        if self.trainable_backbone_layers < 0:
            raise ValueError("trainable_backbone_layers cannot be negative")
        if self.architecture.startswith("custom_") and self.trainable_backbone_layers:
            raise ValueError("Custom CNNs do not have a pretrained backbone")

    @property
    def signature(self) -> str:
        payload = json.dumps(
            asdict(self),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()[:16]

    def mlflow_parameters(self) -> dict[str, Any]:
        return {
            "tuning.architecture": self.architecture,
            "tuning.optimizer": self.optimizer,
            "tuning.learning_rate": self.learning_rate,
            "tuning.batch_size": self.batch_size,
            "tuning.dense_units": self.dense_units,
            "tuning.dropout": self.dropout,
            "tuning.augmentation": self.augmentation,
            "tuning.trainable_backbone_layers": self.trainable_backbone_layers,
        }


@dataclass(frozen=True)
class TuningConfig:
    max_trials: int = 8
    epochs_per_trial: int = 8
    target_val_accuracy: float = 0.95
    min_improvement: float = 0.002
    no_improvement_patience: int = 3
    max_consecutive_failures: int = 3
    early_stopping_patience: int = 2
    pretrained_weights: str = "imagenet"
    architectures: tuple[str, ...] = (
        "efficientnet_b0",
        "mobilenet_v2",
        "custom_augmented",
    )
    study_name: str | None = None
    seed: int = 42

    def __post_init__(self) -> None:
        if self.max_trials < 1 or self.epochs_per_trial < 1:
            raise ValueError("Trial and epoch budgets must be positive")
        if not 0 < self.target_val_accuracy <= 1:
            raise ValueError("target_val_accuracy must be in (0, 1]")
        if self.min_improvement < 0:
            raise ValueError("min_improvement cannot be negative")
        if self.no_improvement_patience < 1 or self.max_consecutive_failures < 1:
            raise ValueError("Patience values must be positive")
        if self.pretrained_weights not in {"imagenet", "none"}:
            raise ValueError("pretrained_weights must be 'imagenet' or 'none'")
        if not self.architectures:
            raise ValueError("At least one architecture is required")
        if len(self.architectures) != len(set(self.architectures)):
            raise ValueError("architectures cannot contain duplicates")
        unknown = set(self.architectures) - set(ARCHITECTURES)
        if unknown:
            raise ValueError(f"Unsupported architectures: {sorted(unknown)}")

    @classmethod
    def from_env(cls, seed: int) -> "TuningConfig":
        architecture_text = os.getenv(
            "CATS_DOGS_TUNER_ARCHITECTURES",
            "efficientnet_b0,mobilenet_v2,custom_augmented",
        )
        architectures = tuple(
            name.strip() for name in architecture_text.split(",") if name.strip()
        )
        return cls(
            max_trials=int(os.getenv("CATS_DOGS_TUNER_MAX_TRIALS", "8")),
            epochs_per_trial=int(os.getenv("CATS_DOGS_TUNER_EPOCHS", "8")),
            target_val_accuracy=float(
                os.getenv("CATS_DOGS_TUNER_TARGET_VAL_ACCURACY", "0.95")
            ),
            min_improvement=float(
                os.getenv("CATS_DOGS_TUNER_MIN_IMPROVEMENT", "0.002")
            ),
            no_improvement_patience=int(
                os.getenv("CATS_DOGS_TUNER_PATIENCE", "3")
            ),
            max_consecutive_failures=int(
                os.getenv("CATS_DOGS_TUNER_MAX_FAILURES", "3")
            ),
            early_stopping_patience=int(
                os.getenv("CATS_DOGS_TUNER_EARLY_STOPPING_PATIENCE", "2")
            ),
            pretrained_weights=os.getenv(
                "CATS_DOGS_TUNER_PRETRAINED_WEIGHTS", "imagenet"
            ).lower(),
            architectures=architectures,
            study_name=os.getenv("CATS_DOGS_TUNER_STUDY_NAME"),
            seed=int(os.getenv("CATS_DOGS_TUNER_SEED", str(seed))),
        )


@dataclass(frozen=True)
class Observation:
    run_id: str
    spec: TrialSpec
    val_accuracy: float
    role: str
    study_name: str | None
    test_evaluated: bool
    test_accuracy: float | None
    model_uri: str | None


@dataclass(frozen=True)
class StudyHistory:
    observations: tuple[Observation, ...]
    attempted_signatures: frozenset[str]

    @property
    def best(self) -> Observation | None:
        return max(self.observations, key=lambda item: item.val_accuracy, default=None)


@dataclass(frozen=True)
class TuningOutcome:
    study_name: str
    source_run_id: str
    best_validation_accuracy: float
    target_reached: bool
    champion_run_id: str
    champion_model_uri: str
    champion_test_accuracy: float
    new_trials: int


def build_search_space(architectures: Iterable[str]) -> list[TrialSpec]:
    specs: list[TrialSpec] = []
    for architecture in architectures:
        if architecture.startswith("custom_"):
            for optimizer in OPTIMIZERS:
                for learning_rate in (0.0001, 0.0003, 0.001):
                    for batch_size in (32, 64):
                        for dense_units in (128, 256):
                            for dropout in (0.2, 0.4):
                                for augmentation in (False, True):
                                    specs.append(
                                        TrialSpec(
                                            architecture=architecture,
                                            optimizer=optimizer,
                                            learning_rate=learning_rate,
                                            batch_size=batch_size,
                                            dense_units=dense_units,
                                            dropout=dropout,
                                            augmentation=augmentation,
                                        )
                                    )
            continue

        for trainable_layers, learning_rates in (
            (0, (0.0001, 0.0003, 0.001)),
            (20, (0.00001, 0.00003, 0.0001)),
        ):
            for learning_rate in learning_rates:
                for batch_size in (32, 64):
                    for dense_units in (128, 256):
                        for dropout in (0.2, 0.4):
                            for augmentation in (False, True):
                                specs.append(
                                    TrialSpec(
                                        architecture=architecture,
                                        optimizer="adam",
                                        learning_rate=learning_rate,
                                        batch_size=batch_size,
                                        dense_units=dense_units,
                                        dropout=dropout,
                                        augmentation=augmentation,
                                        trainable_backbone_layers=trainable_layers,
                                    )
                                )
    return specs


def _priority_specs() -> tuple[TrialSpec, ...]:
    return (
        TrialSpec("efficientnet_b0", "adam", 0.001, 32, 256, 0.2, True),
        TrialSpec("mobilenet_v2", "adam", 0.001, 64, 256, 0.2, True),
        TrialSpec("custom_augmented", "rmsprop", 0.001, 32, 256, 0.2, True),
        TrialSpec("efficientnet_b0", "adam", 0.0001, 32, 256, 0.4, True, 20),
    )


def _spec_features(spec: TrialSpec) -> list[float]:
    return [
        *[float(spec.architecture == name) for name in ARCHITECTURES],
        *[float(spec.optimizer == name) for name in OPTIMIZERS],
        math.log10(spec.learning_rate),
        math.log2(spec.batch_size),
        math.log2(spec.dense_units),
        spec.dropout,
        float(spec.augmentation),
        spec.trainable_backbone_layers / 20.0,
    ]


def select_next_trial(
    search_space: Iterable[TrialSpec],
    observations: Iterable[Observation],
    attempted_signatures: set[str] | frozenset[str],
    *,
    seed: int,
) -> TrialSpec | None:
    observation_list = list(observations)
    evaluated = {item.spec.signature for item in observation_list}
    excluded = set(attempted_signatures) | evaluated
    candidates = [spec for spec in search_space if spec.signature not in excluded]
    if not candidates:
        return None

    if len(observation_list) < 4:
        candidate_signatures = {spec.signature: spec for spec in candidates}
        for preset in _priority_specs():
            if preset.signature in candidate_signatures:
                return candidate_signatures[preset.signature]
        return sorted(candidates, key=lambda item: item.signature)[0]

    features = np.asarray(
        [_spec_features(item.spec) for item in observation_list], dtype="float64"
    )
    targets = np.asarray(
        [item.val_accuracy for item in observation_list], dtype="float64"
    )
    surrogate = RandomForestRegressor(
        n_estimators=128,
        min_samples_leaf=1,
        random_state=seed,
        n_jobs=-1,
    )
    surrogate.fit(features, targets)
    candidate_features = np.asarray(
        [_spec_features(item) for item in candidates], dtype="float64"
    )
    tree_predictions = np.asarray(
        [tree.predict(candidate_features) for tree in surrogate.estimators_]
    )
    acquisition = tree_predictions.mean(axis=0) + 0.5 * tree_predictions.std(axis=0)
    best_index = max(
        range(len(candidates)),
        key=lambda index: (acquisition[index], candidates[index].signature),
    )
    return candidates[best_index]


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).lower() == "true"


def _spec_from_run(run: Any) -> TrialSpec | None:
    params = run.data.params
    tags = run.data.tags
    architecture = params.get("tuning.architecture")
    if architecture is None:
        variant = tags.get("variant") or params.get("model.variant")
        if variant == "baseline":
            architecture = "custom_baseline"
        elif variant == "augmented":
            architecture = "custom_augmented"
        else:
            return None
    try:
        optimizer = params.get(
            "tuning.optimizer", params.get("training.optimizer", "adam")
        ).lower()
        dense_default = 1024 if architecture == "custom_baseline" else 256
        return TrialSpec(
            architecture=architecture,
            optimizer=optimizer,
            learning_rate=float(
                params.get(
                    "tuning.learning_rate",
                    params.get("training.learning_rate", "0.001"),
                )
            ),
            batch_size=int(
                params.get(
                    "tuning.batch_size", params.get("training.batch_size", "32")
                )
            ),
            dense_units=int(params.get("tuning.dense_units", dense_default)),
            dropout=float(params.get("tuning.dropout", "0")),
            augmentation=_parse_bool(
                params.get(
                    "tuning.augmentation", params.get("augmentation.enabled")
                )
            ),
            trainable_backbone_layers=int(
                params.get("tuning.trainable_backbone_layers", "0")
            ),
        )
    except (TypeError, ValueError):
        return None


def load_study_history(
    config: PipelineConfig,
    tracking: TrackingContext,
    dataset: PreparedDataset,
    study_name: str,
) -> StudyHistory:
    client = MlflowClient(tracking_uri=config.tracking_uri)
    runs = client.search_runs(
        experiment_ids=[tracking.experiment_id],
        filter_string="tags.project = 'cats-vs-dogs'",
        order_by=["metrics.best_val_accuracy DESC"],
        max_results=5000,
    )
    observations: list[Observation] = []
    attempted: set[str] = set()
    for run in runs:
        params = run.data.params
        tags = run.data.tags
        if params.get("data.content_sha256") != dataset.content_digest:
            continue
        if params.get("data.split_sha256") != dataset.split_digest:
            continue

        signature = tags.get("tuning.trial_signature")
        same_study = tags.get("tuning.study_name") == study_name
        if (
            signature
            and same_study
            and tags.get("tuning.role") == "trial"
            and run.info.status != "RUNNING"
        ):
            attempted.add(signature)

        score = run.data.metrics.get("best_val_accuracy")
        if (
            score is None
            or not np.isfinite(score)
            or tags.get("run.outcome") != "succeeded"
        ):
            continue
        spec = _spec_from_run(run)
        if spec is None:
            continue
        test_accuracy = run.data.metrics.get("test_accuracy")
        observations.append(
            Observation(
                run_id=run.info.run_id,
                spec=spec,
                val_accuracy=float(score),
                role=tags.get("tuning.role", "legacy"),
                study_name=tags.get("tuning.study_name"),
                test_evaluated=_parse_bool(
                    tags.get("test.evaluated"), test_accuracy is not None
                ),
                test_accuracy=(
                    float(test_accuracy) if test_accuracy is not None else None
                ),
                model_uri=tags.get("model.uri"),
            )
        )
    return StudyHistory(tuple(observations), frozenset(attempted))


def create_tuning_generators(
    config: PipelineConfig,
    dataset: PreparedDataset,
    spec: TrialSpec,
) -> DataGenerators:
    policy = augmentation_policy(spec.augmentation)
    if spec.augmentation:
        training_factory = ImageDataGenerator(
            horizontal_flip=policy["horizontal_flip"],
            rotation_range=policy["rotation_range"],
            width_shift_range=policy["width_shift_range"],
            height_shift_range=policy["height_shift_range"],
            fill_mode=policy["fill_mode"],
        )
    else:
        training_factory = ImageDataGenerator()
    evaluation_factory = ImageDataGenerator()
    common = {
        "target_size": config.image_size,
        "class_mode": "binary",
        "batch_size": spec.batch_size,
    }
    training = training_factory.flow_from_directory(
        str(dataset.training_dir),
        seed=config.seed,
        shuffle=True,
        **common,
    )
    validation = evaluation_factory.flow_from_directory(
        str(dataset.validation_dir),
        shuffle=False,
        **common,
    )
    test = evaluation_factory.flow_from_directory(
        str(dataset.test_dir),
        shuffle=False,
        **common,
    )
    return DataGenerators(
        training=training,
        validation=validation,
        test=test,
        batch_size=spec.batch_size,
        augmentation=policy,
        input_scaling="raw pixel [0,255]; normalization embedded in model",
    )


def _custom_backbone(inputs: Any, architecture: str) -> Any:
    x = tf.keras.layers.Rescaling(1.0 / 255)(inputs)
    x = tf.keras.layers.Conv2D(32, 3, activation="relu")(x)
    x = tf.keras.layers.Conv2D(64, 3, activation="relu")(x)
    x = tf.keras.layers.MaxPooling2D(2)(x)
    x = tf.keras.layers.Conv2D(64, 3, activation="relu")(x)
    x = tf.keras.layers.Conv2D(128, 3, activation="relu")(x)
    x = tf.keras.layers.MaxPooling2D(2)(x)
    x = tf.keras.layers.Conv2D(128, 3, activation="relu")(x)
    x = tf.keras.layers.Conv2D(256, 3, activation="relu")(x)
    if architecture == "custom_augmented":
        x = tf.keras.layers.MaxPooling2D(2)(x)
    return x


def _transfer_backbone(
    inputs: Any,
    spec: TrialSpec,
    image_size: tuple[int, int],
    pretrained_weights: str,
) -> Any:
    weights = None if pretrained_weights == "none" else pretrained_weights
    input_shape = (*image_size, 3)
    if spec.architecture == "mobilenet_v2":
        x = tf.keras.layers.Rescaling(1.0 / 127.5, offset=-1)(inputs)
        backbone = tf.keras.applications.MobileNetV2(
            include_top=False,
            weights=weights,
            input_shape=input_shape,
        )
    elif spec.architecture == "efficientnet_b0":
        x = inputs
        backbone = tf.keras.applications.EfficientNetB0(
            include_top=False,
            weights=weights,
            input_shape=input_shape,
        )
    else:
        raise ValueError(f"Unsupported transfer architecture: {spec.architecture}")

    backbone.trainable = spec.trainable_backbone_layers > 0
    if backbone.trainable:
        frozen_count = max(0, len(backbone.layers) - spec.trainable_backbone_layers)
        for layer in backbone.layers[:frozen_count]:
            layer.trainable = False
        for layer in backbone.layers[frozen_count:]:
            layer.trainable = not isinstance(layer, tf.keras.layers.BatchNormalization)
    return backbone(x, training=False)


def build_tuning_model(
    config: PipelineConfig,
    spec: TrialSpec,
    pretrained_weights: str,
) -> Model:
    tf.keras.utils.set_random_seed(config.seed)
    training_device = configure_tensorflow_runtime(config)
    with tf.device(training_device):
        inputs = tf.keras.layers.Input(shape=(*config.image_size, 3))
        if spec.architecture.startswith("custom_"):
            x = _custom_backbone(inputs, spec.architecture)
        else:
            x = _transfer_backbone(
                inputs,
                spec,
                config.image_size,
                pretrained_weights,
            )
        x = tf.keras.layers.GlobalAveragePooling2D()(x)
        if spec.dropout:
            x = tf.keras.layers.Dropout(spec.dropout)(x)
        x = tf.keras.layers.Dense(spec.dense_units, activation="relu")(x)
        outputs = tf.keras.layers.Dense(len(CLASS_NAMES), activation="softmax")(x)
        model = Model(inputs, outputs, name=f"cats_dogs_{spec.architecture}")
        optimizer_class = {
            "adam": tf.keras.optimizers.Adam,
            "rmsprop": tf.keras.optimizers.RMSprop,
        }[spec.optimizer]
        model.compile(
            optimizer=optimizer_class(learning_rate=spec.learning_rate),
            loss="sparse_categorical_crossentropy",
            metrics=["accuracy"],
        )
    return model


def _resolved_study_name(tuning: TuningConfig, dataset: PreparedDataset) -> str:
    if tuning.study_name:
        return tuning.study_name
    return f"cats-dogs-{dataset.dataset_version}-{SEARCH_VERSION}"


def _trial_context_parameters(
    spec: TrialSpec,
    tuning: TuningConfig,
    history: list[Observation],
    source: Observation | None,
) -> dict[str, Any]:
    parameters = {
        **spec.mlflow_parameters(),
        "tuning.search_version": SEARCH_VERSION,
        "tuning.objective": "best_val_accuracy",
        "tuning.target_val_accuracy": tuning.target_val_accuracy,
        "tuning.history_run_count": len(history),
        "tuning.pretrained_weights": tuning.pretrained_weights,
    }
    if source is not None:
        parameters.update(
            {
                "tuning.parent_run_id": source.run_id,
                "tuning.parent_val_accuracy": source.val_accuracy,
            }
        )
    return parameters


def _trial_tags(
    study_name: str,
    spec: TrialSpec,
    role: str,
) -> dict[str, str]:
    return {
        "execution.type": "auto-tuner",
        "tuning.role": role,
        "tuning.study_name": study_name,
        "tuning.search_version": SEARCH_VERSION,
        "tuning.trial_signature": spec.signature,
    }


def _log_build_failure(
    config: PipelineConfig,
    tracking: TrackingContext,
    dataset: PreparedDataset,
    tuning: TuningConfig,
    study_name: str,
    spec: TrialSpec,
    trial_number: int,
    error: Exception,
) -> str:
    mlflow.set_tracking_uri(config.tracking_uri)
    active_run = mlflow.start_run(
        experiment_id=tracking.experiment_id,
        run_name=(
            f"{config.run_group_id}-tuning-trial-{trial_number:03d}-"
            f"{spec.signature[:8]}"
        ),
        tags={
            "project": "cats-vs-dogs",
            "run_group_id": config.run_group_id,
            "variant": "tuning-trial",
            "dataset_version": dataset.dataset_version,
            **_trial_tags(study_name, spec, "trial"),
        },
    )
    try:
        mlflow.log_params(
            {
                **spec.mlflow_parameters(),
                "tuning.search_version": SEARCH_VERSION,
                "tuning.pretrained_weights": tuning.pretrained_weights,
                "data.content_sha256": dataset.content_digest,
                "data.split_sha256": dataset.split_digest,
            }
        )
        mlflow.set_tags(
            {
                "run.outcome": "failed",
                "failure.type": type(error).__name__,
                "failure.phase": "model-build",
            }
        )
        mlflow.log_text(
            f"{type(error).__name__}: {error}\n",
            "failure/error.txt",
        )
    finally:
        mlflow.end_run(status="FAILED")
    return active_run.info.run_id


def _existing_champion(
    observations: Iterable[Observation],
    study_name: str,
    spec: TrialSpec,
) -> Observation | None:
    matches = [
        item
        for item in observations
        if item.study_name == study_name
        and item.role == "champion"
        and item.spec.signature == spec.signature
        and item.test_evaluated
        and item.model_uri
        and item.test_accuracy is not None
    ]
    return max(matches, key=lambda item: item.val_accuracy, default=None)


def run_auto_tuning(
    config: PipelineConfig,
    tuning: TuningConfig,
    *,
    plan_only: bool = False,
    tracking: TrackingContext | None = None,
    dataset: PreparedDataset | None = None,
) -> TuningOutcome | dict[str, Any]:
    if not plan_only:
        configure_tensorflow_runtime(config)
    tracking = tracking or preflight_tracking(config)
    dataset = dataset or prepare_dataset(config)
    study_name = _resolved_study_name(tuning, dataset)
    study = load_study_history(config, tracking, dataset, study_name)
    observations = list(study.observations)
    attempted = set(study.attempted_signatures)
    search_space = build_search_space(tuning.architectures)
    best = max(observations, key=lambda item: item.val_accuracy, default=None)
    next_spec = select_next_trial(
        search_space,
        observations,
        attempted,
        seed=tuning.seed,
    )

    if plan_only:
        return {
            "study_name": study_name,
            "dataset_version": dataset.dataset_version,
            "historical_runs": len(observations),
            "study_attempts": len(attempted),
            "best_run_id": best.run_id if best else None,
            "best_validation_accuracy": best.val_accuracy if best else None,
            "target_validation_accuracy": tuning.target_val_accuracy,
            "next_trial": asdict(next_spec) if next_spec else None,
        }

    initial_study_attempts = len(attempted)
    no_improvement_count = 0
    consecutive_failures = 0
    while len(attempted) < tuning.max_trials:
        if best is not None and best.val_accuracy >= tuning.target_val_accuracy:
            break
        if no_improvement_count >= tuning.no_improvement_patience:
            break
        if consecutive_failures >= tuning.max_consecutive_failures:
            break
        spec = select_next_trial(
            search_space,
            observations,
            attempted,
            seed=tuning.seed + len(attempted),
        )
        if spec is None:
            break
        attempted.add(spec.signature)
        trial_number = len(attempted)
        trial_config = replace(
            config,
            epochs=tuning.epochs_per_trial,
            baseline_batch_size=spec.batch_size,
            augmented_batch_size=spec.batch_size,
            learning_rate=spec.learning_rate,
            early_stopping_patience=tuning.early_stopping_patience,
        )
        print(
            f"Trial {trial_number}/{tuning.max_trials}: "
            f"{json.dumps(asdict(spec), sort_keys=True)}",
            flush=True,
        )
        generators = None
        model = None
        try:
            generators = create_tuning_generators(trial_config, dataset, spec)
            model = build_tuning_model(
                trial_config,
                spec,
                tuning.pretrained_weights,
            )
        except Exception as error:
            failed_run_id = _log_build_failure(
                config,
                tracking,
                dataset,
                tuning,
                study_name,
                spec,
                trial_number,
                error,
            )
            print(
                f"Trial build failed in MLflow Run {failed_run_id}: {error}",
                file=sys.stderr,
                flush=True,
            )
            consecutive_failures += 1
            model = None
            generators = None
            tf.keras.backend.clear_session()
            continue

        previous_best = best.val_accuracy if best else -math.inf
        try:
            result = run_tracked_training(
                trial_config,
                tracking,
                dataset,
                generators,
                model,
                variant="tuning-trial",
                evaluate_test=False,
                log_model=False,
                selection_metric="val_accuracy",
                extra_parameters=_trial_context_parameters(
                    spec,
                    tuning,
                    observations,
                    best,
                ),
                extra_tags=_trial_tags(study_name, spec, "trial"),
                run_name_suffix=f"-{trial_number:03d}-{spec.signature[:8]}",
                extra_source_paths=[MODULE_PATH],
            )
        except Exception as error:
            print(f"Trial failed: {error}", file=sys.stderr, flush=True)
            consecutive_failures += 1
            model = None
            generators = None
            tf.keras.backend.clear_session()
            continue

        score = float(result.history["val_accuracy"].max())
        observation = Observation(
            run_id=result.run_id,
            spec=spec,
            val_accuracy=score,
            role="trial",
            study_name=study_name,
            test_evaluated=False,
            test_accuracy=None,
            model_uri=None,
        )
        observations.append(observation)
        if score > previous_best:
            best = observation
        if score >= previous_best + tuning.min_improvement:
            no_improvement_count = 0
        else:
            no_improvement_count += 1
        consecutive_failures = 0
        print(
            f"Trial Run {result.run_id}: best_val_accuracy={score:.6f}",
            flush=True,
        )
        del result, model, generators
        tf.keras.backend.clear_session()

    if best is None:
        raise RuntimeError("Auto-tuning produced no successful validation result")

    existing = _existing_champion(observations, study_name, best.spec)
    if existing is not None:
        return TuningOutcome(
            study_name=study_name,
            source_run_id=best.run_id,
            best_validation_accuracy=best.val_accuracy,
            target_reached=best.val_accuracy >= tuning.target_val_accuracy,
            champion_run_id=existing.run_id,
            champion_model_uri=existing.model_uri or "",
            champion_test_accuracy=existing.test_accuracy or 0.0,
            new_trials=len(attempted) - initial_study_attempts,
        )

    champion_config = replace(
        config,
        epochs=tuning.epochs_per_trial,
        baseline_batch_size=best.spec.batch_size,
        augmented_batch_size=best.spec.batch_size,
        learning_rate=best.spec.learning_rate,
        early_stopping_patience=tuning.early_stopping_patience,
    )
    champion_generators = create_tuning_generators(
        champion_config,
        dataset,
        best.spec,
    )
    champion_model = build_tuning_model(
        champion_config,
        best.spec,
        tuning.pretrained_weights,
    )
    champion = run_tracked_training(
        champion_config,
        tracking,
        dataset,
        champion_generators,
        champion_model,
        variant="tuning-champion",
        evaluate_test=True,
        log_model=True,
        selection_metric="val_accuracy",
        extra_parameters={
            **_trial_context_parameters(
                best.spec,
                tuning,
                observations,
                best,
            ),
            "tuning.source_trial_run_id": best.run_id,
            "tuning.source_val_accuracy": best.val_accuracy,
        },
        extra_tags=_trial_tags(study_name, best.spec, "champion"),
        run_name_suffix=f"-{best.spec.signature[:8]}",
        extra_source_paths=[MODULE_PATH],
    )
    if champion.model_uri is None:
        raise RuntimeError("Champion Run did not produce an MLflow model URI")
    return TuningOutcome(
        study_name=study_name,
        source_run_id=best.run_id,
        best_validation_accuracy=best.val_accuracy,
        target_reached=best.val_accuracy >= tuning.target_val_accuracy,
        champion_run_id=champion.run_id,
        champion_model_uri=champion.model_uri,
        champion_test_accuracy=champion.test_metrics["test_accuracy"],
        new_trials=len(attempted) - initial_study_attempts,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tune cats-vs-dogs models from compatible MLflow history."
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="prepare data and print the next history-aware trial without training",
    )
    arguments = parser.parse_args()
    pipeline_config = PipelineConfig.from_env()
    tuning_config = TuningConfig.from_env(pipeline_config.seed)
    outcome = run_auto_tuning(
        pipeline_config,
        tuning_config,
        plan_only=arguments.plan_only,
    )
    payload = asdict(outcome) if isinstance(outcome, TuningOutcome) else outcome
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
