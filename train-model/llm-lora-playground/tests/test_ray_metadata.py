from pathlib import Path
import json
import tempfile
import unittest

from llm_lora_playground.job_metadata import (
    build_job_metadata,
    update_checkpoint_pointer,
    update_job_status,
    write_job_metadata_atomic,
)


class RayMetadataTests(unittest.TestCase):
    def test_metadata_is_schema_shaped_and_atomic(self):
        metadata = build_job_metadata(
            ray_job_id="job-1", mlflow_run_id="run-1", config_digest="a" * 64,
            dataset_manifest_digest="b" * 64, code_revision="git:abc",
            environment_digest="c" * 64, attempt_id="attempt-1",
            requested_resources={"num_gpus": 1, "cpus": 4, "memory_gb": 8},
            ray_submission_id="submission-1", project="llm-lora-playground",
            run_kind="ray_smoke", role="smoke", split_digest="e" * 64,
            preprocessing_version="toy-sft-v1", execution_mode="governed-ray-job",
            promotable=False,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.json"
            write_job_metadata_atomic(metadata, path)
            loaded = json.loads(path.read_text())
            self.assertEqual(loaded["status"], "submitted")
            self.assertFalse(loaded["promotable"])
            updated = update_checkpoint_pointer(loaded, {"uri": "x", "digest": "d" * 64})
            self.assertEqual(updated["checkpoint_uri"], "x")
            completed = update_job_status(updated, "completed")
            self.assertEqual(completed["status"], "completed")


if __name__ == "__main__":
    unittest.main()
