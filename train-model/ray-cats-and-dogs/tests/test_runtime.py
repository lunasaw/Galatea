from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import ray.cloudpickle as cloudpickle  # noqa: E402
from ray_cats_dogs.input_pipeline import decode_image_batch  # noqa: E402
from ray_cats_dogs.runtime import (  # noqa: E402
    RAY_JOB_CONFIG_ENV,
    build_runtime_env,
    controller_pickle_by_value,
    ray_init_runtime_env,
    worker_runtime_env,
)
from ray_cats_dogs.tracking import RayMlflowCallback  # noqa: E402
from ray_cats_dogs.worker import train_loop_per_worker  # noqa: E402


class RuntimeEnvTest(unittest.TestCase):
    def test_project_directory_and_src_package_are_uploaded(self) -> None:
        runtime_env = build_runtime_env(PROJECT_ROOT)

        self.assertEqual(str(PROJECT_ROOT), runtime_env["working_dir"])
        self.assertEqual(
            [str(PROJECT_ROOT / "src" / "ray_cats_dogs")],
            runtime_env["py_modules"],
        )
        self.assertIn("notebooks/**", runtime_env["excludes"])

    def test_ray_job_uses_its_injected_runtime_env(self) -> None:
        self.assertIsNone(
            ray_init_runtime_env(
                PROJECT_ROOT, {RAY_JOB_CONFIG_ENV: '{"runtime_env": {}}'}
            )
        )
        self.assertEqual(
            build_runtime_env(PROJECT_ROOT),
            ray_init_runtime_env(PROJECT_ROOT, {}),
        )

    def test_uploaded_runtime_env_is_forwarded_to_workers(self) -> None:
        runtime_context = SimpleNamespace(
            runtime_env={
                "working_dir": "gcs://project.zip",
                "py_modules": ["gcs://src.zip"],
                "excludes": ["tests/**"],
            }
        )
        ray_module = SimpleNamespace(
            get_runtime_context=lambda: runtime_context
        )

        forwarded = worker_runtime_env(ray_module)

        self.assertEqual("gcs://project.zip", forwarded["working_dir"])
        self.assertEqual(["gcs://src.zip"], forwarded["py_modules"])
        self.assertNotIn("excludes", forwarded)

    def test_missing_worker_package_fails_before_training(self) -> None:
        ray_module = SimpleNamespace(
            get_runtime_context=lambda: SimpleNamespace(
                runtime_env={"working_dir": "gcs://project.zip"}
            )
        )

        with self.assertRaisesRegex(RuntimeError, "missing py_modules"):
            worker_runtime_env(ray_module)

    def test_controller_payload_loads_without_project_on_pythonpath(self) -> None:
        callback = RayMlflowCallback("http://tracking", "run-123")
        with controller_pickle_by_value():
            payload = cloudpickle.dumps(
                (train_loop_per_worker, callback, decode_image_batch)
            )

        with tempfile.TemporaryDirectory() as directory:
            payload_path = Path(directory) / "controller-payload.pkl"
            payload_path.write_bytes(payload)
            environment = dict(os.environ)
            environment.pop("PYTHONPATH", None)
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import pickle,sys; "
                        "function,callback,decoder=pickle.load(open(sys.argv[1],'rb')); "
                        "print(function.__name__,type(callback).__name__,"
                        "callback.run_id,decoder.__name__)"
                    ),
                    str(payload_path),
                ],
                cwd=directory,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "train_loop_per_worker RayMlflowCallback run-123 decode_image_batch",
            result.stdout.strip(),
        )


if __name__ == "__main__":
    unittest.main()
