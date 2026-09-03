import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import torch
from torch.utils.data import DataLoader, TensorDataset

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import cats_dogs_torch_pipeline as pipeline


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


class TorchRuntimeTests(unittest.TestCase):
    def test_requires_cuda_by_default(self) -> None:
        config = _config(require_gpu=True)
        with patch.object(pipeline.torch.cuda, "is_available", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "did not register a CUDA GPU"):
                pipeline.configure_torch_runtime(config)

    def test_allows_explicit_cpu_smoke_override(self) -> None:
        config = _config(require_gpu=False)
        with patch.object(pipeline.torch.cuda, "is_available", return_value=False):
            self.assertEqual(
                torch.device("cpu"), pipeline.configure_torch_runtime(config)
            )

    def test_selects_first_cuda_device_and_seeds_it(self) -> None:
        config = _config(require_gpu=True)
        with (
            patch.object(pipeline.torch.cuda, "is_available", return_value=True),
            patch.object(pipeline.torch.cuda, "set_device") as set_device,
            patch.object(pipeline.torch.cuda, "manual_seed_all") as cuda_seed,
        ):
            device = pipeline.configure_torch_runtime(config)

        self.assertEqual(torch.device("cuda:0"), device)
        set_device.assert_called_once_with(torch.device("cuda:0"))
        cuda_seed.assert_called_with(config.seed)

    def test_cnn_returns_two_logits_on_cpu(self) -> None:
        model = pipeline.CatsDogsCNN("baseline")
        outputs = model(torch.zeros((2, 3, 64, 64), dtype=torch.float32))

        self.assertEqual((2, 2), tuple(outputs.shape))
        self.assertGreater(model.count_params(), 0)

    def test_gradcam_handles_cnn_backward_hook(self) -> None:
        model = pipeline.CatsDogsCNN("baseline")
        images = torch.rand((4, 3, 64, 64), dtype=torch.float32)
        loader = DataLoader(
            TensorDataset(images, torch.zeros(4, dtype=torch.long)), batch_size=4
        )
        with torch.no_grad():
            desired_class = int(model(images[:1]).argmax(dim=1).item())
        result = pipeline.ExperimentResult(
            run_id="gradcam-test",
            model_uri=None,
            artifact_uri="file:///tmp",
            model=model,
            history=None,
            test_metrics={},
            predictions=None,
            confusion_matrix=None,
            quality_gate_passed=None,
        )

        figure = pipeline.plot_gradcam_examples(
            result, loader, desired_class=desired_class, n_images=1
        )
        pipeline.plt.close(figure)


if __name__ == "__main__":
    unittest.main()
