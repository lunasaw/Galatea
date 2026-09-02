from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import torch
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_cats_dogs.config import load_config  # noqa: E402
from ray_cats_dogs.models import build_model  # noqa: E402
from ray_cats_dogs.tracking import _flatten  # noqa: E402
from ray_cats_dogs.train import _log_mlflow_model  # noqa: E402


class GalateaContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = yaml.safe_load(
            (PROJECT_ROOT / "galatea.project.yaml").read_text(encoding="utf-8")
        )
        cls.spec = cls.manifest["spec"]

    def test_manifest_declares_the_actual_objective_and_fixed_entrypoints(self) -> None:
        baseline = load_config(PROJECT_ROOT / "configs" / "baseline.yaml")

        self.assertEqual("galatea/v1", self.manifest["apiVersion"])
        self.assertEqual("TrainingProject", self.manifest["kind"])
        self.assertEqual("ray-cats-and-dogs", self.manifest["metadata"]["name"])
        self.assertEqual(
            {
                "metric": baseline.training.objective_metric,
                "direction": baseline.training.objective_mode,
            },
            self.spec["objective"],
        )
        self.assertEqual(
            ["python", "scripts/train.py", "--config", "{config}", "--check-config"],
            self.spec["entrypoints"]["checkConfig"],
        )
        self.assertEqual(
            ["python", "scripts/train.py", "--config", "{config}", "--plan"],
            self.spec["entrypoints"]["plan"],
        )
        self.assertEqual(
            ["python", "scripts/train.py", "--config", "{config}"],
            self.spec["entrypoints"]["train"],
        )
        self.assertFalse(self.spec["capabilities"]["pauseResume"])

    def test_manifest_preserves_trial_and_champion_test_isolation(self) -> None:
        trial = load_config(PROJECT_ROOT / "configs" / "baseline.yaml")
        champion = load_config(PROJECT_ROOT / "configs" / "champion.yaml")

        self.assertEqual("trial", trial.run.role)
        self.assertFalse(trial.evaluation.evaluate_test)
        self.assertFalse(trial.run.log_model)
        self.assertEqual("champion", champion.run.role)
        self.assertTrue(champion.evaluation.evaluate_test)
        self.assertTrue(champion.run.log_model)

        compatibility = self.spec["runEvidence"]["compatibility"]
        self.assertEqual(
            {"source": "param", "key": "data.content_sha256"},
            compatibility["datasetDigest"],
        )
        self.assertEqual(
            {"source": "param", "key": "data.split_sha256"},
            compatibility["splitDigest"],
        )
        self.assertEqual(
            {"source": "param", "key": "data.preprocessing_version"},
            compatibility["preprocessingVersion"],
        )
        self.assertEqual(
            {"source": "tag", "key": "run.role"},
            compatibility["role"],
        )

    def test_manifest_artifacts_and_tags_match_training_output(self) -> None:
        train_source = (PROJECT_ROOT / "src" / "ray_cats_dogs" / "train.py").read_text(
            encoding="utf-8"
        )
        evidence = self.spec["runEvidence"]
        parameters = {}
        _flatten(
            "",
            load_config(PROJECT_ROOT / "configs" / "baseline.yaml").as_dict(),
            parameters,
        )

        self.assertEqual(
            {
                "run.outcome": "succeeded",
                "artifact.roundtrip_verified": "true",
            },
            evidence["requiredTags"],
        )
        self.assertEqual(
            ["reports/model-selection.json"],
            evidence["stageArtifacts"]["training-optimization"],
        )
        self.assertEqual(
            ["reports/final-test-evaluation.json", "model/MLmodel"],
            evidence["stageArtifacts"]["final-validation"],
        )
        self.assertEqual(
            {"artifactPath": "model", "uriTag": "model.uri"},
            evidence["modelSource"],
        )
        self.assertIn("data.preprocessing_version", parameters)
        tracking_source = (
            PROJECT_ROOT / "src" / "ray_cats_dogs" / "tracking.py"
        ).read_text(encoding="utf-8")
        self.assertIn("data.content_sha256", tracking_source)
        self.assertIn("data.split_sha256", tracking_source)
        for literal in (
            "reports/model-selection.json",
            "reports/final-test-evaluation.json",
            "model/MLmodel",
            'mlflow.set_tag("model.uri", model_uri)',
            'f"runs:/{run_id}/model"',
        ):
            self.assertIn(literal, train_source)

    def test_champion_model_is_republished_as_a_verified_run_artifact(self) -> None:
        config = load_config(PROJECT_ROOT / "configs" / "champion.yaml")
        model_config = {
            "family": "custom_cnn",
            "dense_units": 8,
            "dropout": 0.0,
            "augmentation": False,
            "pretrained_weights": None,
        }
        training_config = {"optimizer": "adam", "learning_rate": 0.001}
        image_size = (32, 32)
        seed = 7
        model = build_model(model_config, training_config, image_size, seed)

        with tempfile.TemporaryDirectory() as directory:
            checkpoint_directory = Path(directory) / "checkpoint"
            logged_model_directory = Path(directory) / "logged-model"
            run_descriptor = Path(directory) / "run-model" / "MLmodel"
            checkpoint_directory.mkdir()
            logged_model_directory.mkdir()
            run_descriptor.parent.mkdir()
            (logged_model_directory / "MLmodel").write_text(
                "artifact_path: models:/m-123\n", encoding="utf-8"
            )
            run_descriptor.write_text("artifact_path: model\n", encoding="utf-8")
            torch.save(
                {
                    "model_config": model_config,
                    "training_config": training_config,
                    "image_size": image_size,
                    "seed": seed,
                    "model_state_dict": model.state_dict(),
                },
                checkpoint_directory / "best-model.pt",
            )
            checkpoint = SimpleNamespace(
                as_directory=lambda: nullcontext(str(checkpoint_directory))
            )

            with (
                patch(
                    "mlflow.pytorch.log_model",
                    return_value=SimpleNamespace(model_uri="models:/m-123"),
                ),
                patch(
                    "mlflow.artifacts.download_artifacts",
                    side_effect=[str(logged_model_directory), str(run_descriptor)],
                ) as download,
                patch("mlflow.log_artifacts") as log_artifacts,
            ):
                model_uri = _log_mlflow_model(checkpoint, config, "run-123")

        self.assertEqual("runs:/run-123/model", model_uri)
        log_artifacts.assert_called_once_with(
            str(logged_model_directory), artifact_path="model"
        )
        self.assertEqual("models:/m-123", download.call_args_list[0].kwargs["artifact_uri"])
        self.assertEqual("run-123", download.call_args_list[1].kwargs["run_id"])
        self.assertEqual(
            "model/MLmodel", download.call_args_list[1].kwargs["artifact_path"]
        )


if __name__ == "__main__":
    unittest.main()
