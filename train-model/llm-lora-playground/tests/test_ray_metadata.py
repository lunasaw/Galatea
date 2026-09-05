from pathlib import Path
import json
import tempfile
import unittest

from llm_lora_playground.job_metadata import (
    build_job_metadata,
    update_checkpoint_pointer,
    write_job_metadata_atomic,
)


class RayMetadataTests(unittest.TestCase):
    def test_metadata_is_schema_shaped_and_atomic(self):
        metadata = build_job_metadata(
            ray_job_id="job-1", mlflow_run_id="run-1", config_digest="a" * 64,
            dataset_manifest_digest="b" * 64, code_revision="git:abc",
            environment_digest="c" * 64, attempt_id="attempt-1",
            requested_resources={"num_gpus": 1, "cpus": 4, "memory_gb": 8},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.json"
            write_job_metadata_atomic(metadata, path)
            loaded = json.loads(path.read_text())
            self.assertEqual(loaded["status"], "submitted")
            updated = update_checkpoint_pointer(loaded, {"uri": "x", "digest": "d" * 64})
            self.assertEqual(updated["checkpoint_uri"], "x")


if __name__ == "__main__":
    unittest.main()
