import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cats_dogs_pipeline as pipeline


def _config(*, require_gpu: bool) -> pipeline.PipelineConfig:
    return pipeline.PipelineConfig(
        repo_root=Path("/tmp/cats-dogs-tests"),
        data_dir=Path("/tmp/cats-dogs-tests/data"),
        split_root=Path("/tmp/cats-dogs-tests/splits"),
        notebook_path=Path("/tmp/cats-dogs-tests/notebook.ipynb"),
        require_gpu=require_gpu,
        expected_images_per_class=None,
        expected_valid_images=None,
    )


class TensorFlowRuntimeTests(unittest.TestCase):
    def test_requires_a_logical_gpu_by_default(self) -> None:
        config = _config(require_gpu=True)
        with patch.object(pipeline.tf.config, "list_physical_devices", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "did not register a GPU"):
                pipeline.configure_tensorflow_runtime(config)

    def test_allows_explicit_cpu_smoke_override(self) -> None:
        config = _config(require_gpu=False)
        with patch.object(pipeline.tf.config, "list_physical_devices", return_value=[]):
            self.assertEqual(pipeline.configure_tensorflow_runtime(config), "/CPU:0")

    def test_selects_first_logical_gpu_and_enables_memory_growth(self) -> None:
        config = _config(require_gpu=True)
        physical_gpu = SimpleNamespace(name="/physical_device:GPU:0")
        logical_gpu = SimpleNamespace(name="/device:GPU:0")
        with (
            patch.object(
                pipeline.tf.config,
                "list_physical_devices",
                return_value=[physical_gpu],
            ),
            patch.object(
                pipeline.tf.config,
                "list_logical_devices",
                return_value=[logical_gpu],
            ),
            patch.object(
                pipeline.tf.config.experimental, "set_memory_growth"
            ) as set_growth,
        ):
            self.assertEqual(
                pipeline.configure_tensorflow_runtime(config), "/device:GPU:0"
            )
        set_growth.assert_called_once_with(physical_gpu, True)

    def test_rejects_physical_gpu_that_failed_logical_initialization(self) -> None:
        config = _config(require_gpu=True)
        physical_gpu = SimpleNamespace(name="/physical_device:GPU:0")
        with (
            patch.object(
                pipeline.tf.config,
                "list_physical_devices",
                return_value=[physical_gpu],
            ),
            patch.object(pipeline.tf.config, "list_logical_devices", return_value=[]),
            patch.object(pipeline.tf.config.experimental, "set_memory_growth"),
        ):
            with self.assertRaisesRegex(RuntimeError, "physical GPU hardware"):
                pipeline.configure_tensorflow_runtime(config)


if __name__ == "__main__":
    unittest.main()
