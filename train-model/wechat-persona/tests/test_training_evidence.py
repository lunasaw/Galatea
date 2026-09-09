import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.training import _artifact_parent, _log_training_history


class FakeClient:
    def __init__(self):
        self.batches = []

    def log_batch(self, run_id, *, metrics, synchronous):
        self.batches.append((run_id, list(metrics), synchronous))


class TrainingEvidenceTests(unittest.TestCase):
    def test_training_history_uses_bounded_synchronous_batches(self):
        history = [
            {"step": step, "loss": step / 10, "learning_rate": 0.001, "grad_norm": 1.0}
            for step in range(401)
        ]
        client = FakeClient()

        _log_training_history(client, "run-1", history)

        self.assertEqual([1000, 203], [len(batch[1]) for batch in client.batches])
        self.assertTrue(all(batch[0] == "run-1" and batch[2] is True for batch in client.batches))
        metrics = [metric for _, batch, _ in client.batches for metric in batch]
        self.assertEqual({"train_loss", "learning_rate", "gradient_norm"}, {m.key for m in metrics})
        self.assertEqual(400, metrics[-1].step)

    def test_artifact_parent_requires_exact_remote_filename(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "best-adapter.safetensors"
            source.write_bytes(b"adapter")
            self.assertEqual("checkpoints", _artifact_parent(source, "checkpoints/best-adapter.safetensors"))
            with self.assertRaisesRegex(ValueError, "does not match"):
                _artifact_parent(source, "checkpoints/adapter_model.safetensors")
            with self.assertRaisesRegex(ValueError, "cannot traverse"):
                _artifact_parent(source, "../best-adapter.safetensors")


if __name__ == "__main__":
    unittest.main()
