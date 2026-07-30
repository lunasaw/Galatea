from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace


MODULE_DIR = (
    Path(__file__).resolve().parents[1] / "train-model" / "cats-and-dogs"
)
sys.path.insert(0, str(MODULE_DIR))

from cats_dogs_tuner import (  # noqa: E402
    Observation,
    TrialSpec,
    TuningConfig,
    _spec_from_run,
    build_search_space,
    select_next_trial,
)


def _observation(run_id: str, spec: TrialSpec, score: float) -> Observation:
    return Observation(
        run_id=run_id,
        spec=spec,
        val_accuracy=score,
        role="trial",
        study_name="test-study",
        test_evaluated=False,
        test_accuracy=None,
        model_uri=None,
    )


class TrialSpecTest(unittest.TestCase):
    def test_signature_is_stable_and_changes_with_parameters(self) -> None:
        spec = TrialSpec(
            "custom_augmented", "adam", 0.001, 32, 256, 0.2, True
        )

        self.assertEqual(spec.signature, replace(spec).signature)
        self.assertNotEqual(spec.signature, replace(spec, dropout=0.4).signature)

    def test_search_space_has_unique_trials(self) -> None:
        search_space = build_search_space(
            ("efficientnet_b0", "mobilenet_v2", "custom_augmented")
        )

        self.assertEqual(288, len(search_space))
        self.assertEqual(
            len(search_space), len({spec.signature for spec in search_space})
        )

    def test_reads_the_existing_augmented_run_schema(self) -> None:
        run = SimpleNamespace(
            data=SimpleNamespace(
                params={
                    "training.optimizer": "RMSprop",
                    "training.learning_rate": "0.001",
                    "training.batch_size": "32",
                    "augmentation.enabled": "True",
                },
                tags={"variant": "augmented"},
            )
        )

        spec = _spec_from_run(run)

        self.assertIsNotNone(spec)
        self.assertEqual("custom_augmented", spec.architecture)
        self.assertEqual("rmsprop", spec.optimizer)
        self.assertTrue(spec.augmentation)


class TrialSelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.search_space = build_search_space(
            ("efficientnet_b0", "mobilenet_v2", "custom_augmented")
        )

    def test_cold_start_uses_transfer_learning_priority(self) -> None:
        selected = select_next_trial(self.search_space, [], set(), seed=42)

        self.assertIsNotNone(selected)
        self.assertEqual("efficientnet_b0", selected.architecture)
        self.assertEqual(0, selected.trainable_backbone_layers)

    def test_attempted_trial_is_not_selected_again(self) -> None:
        first = select_next_trial(self.search_space, [], set(), seed=42)
        selected = select_next_trial(
            self.search_space,
            [],
            {first.signature},
            seed=42,
        )

        self.assertIsNotNone(selected)
        self.assertNotEqual(first.signature, selected.signature)
        self.assertEqual("mobilenet_v2", selected.architecture)

    def test_surrogate_returns_an_unseen_trial(self) -> None:
        specs = self.search_space[:4]
        observations = [
            _observation(f"run-{index}", spec, 0.70 + index / 100)
            for index, spec in enumerate(specs)
        ]
        selected = select_next_trial(
            self.search_space,
            observations,
            set(),
            seed=42,
        )

        self.assertIsNotNone(selected)
        self.assertNotIn(selected.signature, {spec.signature for spec in specs})


class TuningConfigTest(unittest.TestCase):
    def test_rejects_an_impossible_accuracy_target(self) -> None:
        with self.assertRaises(ValueError):
            TuningConfig(target_val_accuracy=1.01)

    def test_rejects_duplicate_architectures(self) -> None:
        with self.assertRaises(ValueError):
            TuningConfig(architectures=("mobilenet_v2", "mobilenet_v2"))


if __name__ == "__main__":
    unittest.main()
