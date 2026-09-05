from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_kaggle_house_prices.job_release import build_runtime_environment  # noqa: E402


class JobReleaseEnvironmentTest(unittest.TestCase):
    def test_conda_is_the_default_and_is_hashed(self) -> None:
        runtime_env, identity = build_runtime_environment(PROJECT_ROOT)

        self.assertIn("conda", runtime_env)
        self.assertNotIn("pip", runtime_env)
        self.assertEqual("conda", identity["runtime_mode"])
        self.assertEqual("conda.yaml", identity["environment_source"])
        self.assertEqual(64, len(identity["environment_sha256"]))

    def test_pip_mode_is_explicit_and_disjoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            requirements = Path(directory) / "requirements.txt"
            requirements.write_text("xgboost==3.0.5\n", encoding="utf-8")
            runtime_env, identity = build_runtime_environment(
                PROJECT_ROOT,
                runtime_mode="pip",
                pip_requirements=requirements,
            )

        self.assertNotIn("conda", runtime_env)
        self.assertEqual(["xgboost==3.0.5"], runtime_env["pip"]["packages"])
        self.assertEqual("pip", identity["runtime_mode"])

    def test_pip_mode_without_input_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicit pip requirements or packages"):
            build_runtime_environment(PROJECT_ROOT, runtime_mode="pip")


if __name__ == "__main__":
    unittest.main()
