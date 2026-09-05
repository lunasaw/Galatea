from pathlib import Path
import tempfile
import unittest

from llm_lora_playground.checkpoints import (
    CheckpointContractError,
    save_checkpoint,
    verify_checkpoint,
)


class CheckpointMetadataTests(unittest.TestCase):
    def test_checkpoint_is_complete_and_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            record = save_checkpoint(
                {"adapter_model.safetensors": b"adapter", "adapter_config.json": b"{}"},
                Path(directory),
                {"step": 2, "config_digest": "a" * 64, "dataset_manifest_digest": "b" * 64},
            )
            self.assertEqual(record.status, "complete")
            verify_checkpoint(record)
            self.assertTrue((record.path / "checkpoint_manifest.json").is_file())

    def test_incomplete_checkpoint_cannot_verify(self):
        with tempfile.TemporaryDirectory() as directory:
            record = save_checkpoint({"adapter.bin": b"x"}, Path(directory), {"step": 1}, mark_complete=False)
            with self.assertRaises(CheckpointContractError):
                verify_checkpoint(record)


if __name__ == "__main__":
    unittest.main()
