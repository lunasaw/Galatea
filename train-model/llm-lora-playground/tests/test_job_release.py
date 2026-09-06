from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from llm_lora_playground.job_release import build_release, create_working_dir_archive


ROOT = Path(__file__).resolve().parents[1]


class JobReleaseTests(unittest.TestCase):
    def test_release_is_content_addressed_and_uses_project_python(self):
        with tempfile.TemporaryDirectory() as directory:
            first = build_release(ROOT, Path(directory))
            second = build_release(ROOT, Path(directory))
        self.assertEqual(first.manifest["release_id"], second.manifest["release_id"])
        self.assertEqual(
            "/data/conda/envs/llm-lora-ray-py312/bin/python",
            first.manifest["runtime_env"]["py_executable"],
        )
        self.assertEqual(str(ROOT.parents[1]), first.manifest["runtime_env"]["env_vars"]["GALATEA_REPOSITORY_ROOT"])
        self.assertEqual("http://127.0.0.1:5000", first.manifest["runtime_env"]["env_vars"]["MLFLOW_TRACKING_URI"])
        self.assertNotIn("py_modules", first.manifest["runtime_env"])
        self.assertIn("py_module", first.manifest["files"])
        self.assertIn("working_dir", first.manifest["files"])


if __name__ == "__main__":
    unittest.main()
