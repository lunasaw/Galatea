from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

import mlflow.pytorch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_cats_dogs.model_artifacts import (  # noqa: E402
    model_code_paths,
    verify_packaged_model,
)
from ray_cats_dogs.models import build_model  # noqa: E402


class ModelArtifactTest(unittest.TestCase):
    def test_saved_package_loads_in_isolated_process_without_project_path(self) -> None:
        # Forward-only fixture: no optimizer, dataset, MLflow Run, or test access.
        model = build_model(
            {
                "family": "custom_cnn",
                "dense_units": 8,
                "dropout": 0.0,
                "augmentation": False,
                "pretrained_weights": None,
            },
            {"optimizer": "adam", "learning_rate": 0.001},
            (32, 32),
            seed=7,
        ).eval()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "model"
            mlflow.pytorch.save_model(
                model,
                str(destination),
                code_paths=model_code_paths(),
                pip_requirements=[],
                serialization_format="pickle",
            )
            self.assertTrue((destination / "code/ray_cats_dogs/__init__.py").is_file())
            self.assertFalse((destination / "code/src").exists())
            result = verify_packaged_model(destination, (32, 32))
        self.assertTrue(result["default_loader_verified"])
        self.assertTrue(result["package_from_model_artifact"])
        self.assertTrue(result["isolated_process"])
        self.assertFalse(result["test_data_read"])
        self.assertEqual([1, 2], result["output_shape"])


if __name__ == "__main__":
    unittest.main()
