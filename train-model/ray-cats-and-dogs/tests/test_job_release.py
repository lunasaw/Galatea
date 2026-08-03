from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_cats_dogs.job_release import (  # noqa: E402
    BuiltRelease,
    build_release,
    build_training_entrypoint,
    ci_main,
    create_working_dir_archive,
    deploy_release_manifest,
    generate_submission_id,
    load_aws_environment,
    load_release_manifest,
    publish_release,
    submit_release,
)


class JobReleaseTest(unittest.TestCase):
    def _release_fixture(self, directory: str) -> BuiltRelease:
        release_directory = Path(directory)
        return BuiltRelease(
            directory=release_directory,
            manifest={"release_id": "release-123"},
            manifest_path=release_directory / "release.json",
            runtime_env_path=release_directory / "runtime-env.yaml",
        )

    def test_working_dir_archive_contains_only_runtime_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "working-dir.zip"
            create_working_dir_archive(PROJECT_ROOT, archive_path)
            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())

        self.assertIn("scripts/train.py", names)
        self.assertIn("configs/smoke.yaml", names)
        self.assertIn("src/ray_cats_dogs/worker.py", names)
        self.assertFalse(any(name.startswith("job/") for name in names))
        self.assertFalse(any(name.startswith("notebooks/") for name in names))
        self.assertFalse(any(name.startswith("tests/") for name in names))
        self.assertFalse(any("__pycache__" in name for name in names))

    def test_release_uses_s3_zip_and_wheel_runtime_env(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            release = build_release(
                PROJECT_ROOT,
                Path(directory),
                bucket="training-data",
                prefix="ray-runtime/test",
            )
            manifest = load_release_manifest(release.manifest_path)
            persisted = json.loads(release.manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(persisted, manifest)
            self.assertTrue(manifest["runtime_env"]["working_dir"].endswith(".zip"))
            self.assertTrue(manifest["runtime_env"]["py_modules"][0].endswith(".whl"))
            self.assertIn(manifest["release_id"], manifest["runtime_env"]["working_dir"])
            self.assertTrue(release.runtime_env_path.is_file())

            second_release = build_release(
                PROJECT_ROOT,
                Path(directory),
                bucket="training-data",
                prefix="ray-runtime/test",
            )
            self.assertEqual(
                release.manifest["release_id"],
                second_release.manifest["release_id"],
            )

            extra_file = release.directory / "must-not-upload.txt"
            extra_file.write_text("not part of the release", encoding="utf-8")

            class MissingObject(Exception):
                response = {
                    "Error": {"Code": "NoSuchKey"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                }

            class FakeS3Client:
                def __init__(self) -> None:
                    self.keys: list[str] = []

                def head_bucket(self, **kwargs) -> None:
                    self.bucket = kwargs["Bucket"]

                def head_object(self, **kwargs) -> None:
                    raise MissingObject

                def put_object(self, **kwargs) -> None:
                    kwargs["Body"].read()
                    self.keys.append(kwargs["Key"])

            client = FakeS3Client()
            with patch(
                "ray_cats_dogs.job_release._s3_client",
                return_value=client,
            ):
                statuses = publish_release(release, "http://127.0.0.1:9000")

            self.assertEqual("training-data", client.bucket)
            self.assertEqual(4, len(statuses))
            self.assertFalse(
                any(key.endswith("must-not-upload.txt") for key in client.keys)
            )

    def test_aws_environment_file_does_not_override_explicit_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / "s3.env"
            env_file.write_text(
                "AWS_ACCESS_KEY_ID=file-user\n"
                "AWS_SECRET_ACCESS_KEY=file-secret\n"
                "AWS_ENDPOINT_URL_S3=http://127.0.0.1:9000\n"
                "IGNORED=value\n",
                encoding="utf-8",
            )
            environment = {"AWS_ACCESS_KEY_ID": "explicit-user"}
            load_aws_environment(env_file, environment)

        self.assertEqual("explicit-user", environment["AWS_ACCESS_KEY_ID"])
        self.assertEqual("file-secret", environment["AWS_SECRET_ACCESS_KEY"])
        self.assertNotIn("IGNORED", environment)

    def test_training_entrypoint_defaults_to_non_training_check(self) -> None:
        entrypoint = build_training_entrypoint(
            "configs/smoke.yaml",
            "check-config",
            overrides=("training.learning_rate=0.0003",),
        )

        self.assertEqual(
            "python scripts/train.py --config configs/smoke.yaml "
            "--set training.learning_rate=0.0003 --check-config",
            entrypoint,
        )

    def test_generated_submission_id_is_readable_and_unique(self) -> None:
        with patch(
            "ray_cats_dogs.job_release.secrets.token_hex",
            side_effect=("0123abcd", "4567efab"),
        ):
            first = generate_submission_id("configs/My Baseline @2.yaml", "train")
            second = generate_submission_id("configs/My Baseline @2.yaml", "train")

        self.assertRegex(
            first,
            r"^ray-cats-dogs-my-baseline-2-train-\d{8}t\d{6}z-0123abcd$",
        )
        self.assertRegex(
            second,
            r"^ray-cats-dogs-my-baseline-2-train-\d{8}t\d{6}z-4567efab$",
        )
        self.assertNotEqual(first, second)

    def test_deploy_generates_id_unless_explicitly_provided(self) -> None:
        manifest = {
            "runtime_env": {"working_dir": "s3://training-data/release.zip"},
        }
        with (
            patch(
                "ray_cats_dogs.job_release.load_release_manifest",
                return_value=manifest,
            ),
            patch(
                "ray_cats_dogs.job_release.generate_submission_id",
                return_value="generated-id",
            ) as generate_mock,
        ):
            generated = deploy_release_manifest(
                Path("release.json"),
                address="http://127.0.0.1:8265",
                submission_id=None,
                config="configs/baseline.yaml",
                mode="train",
                dry_run=True,
            )
            explicit = deploy_release_manifest(
                Path("release.json"),
                address="http://127.0.0.1:8265",
                submission_id="explicit-id",
                config="configs/baseline.yaml",
                mode="train",
                dry_run=True,
            )

        self.assertEqual("generated-id", generated["submission_id"])
        self.assertEqual("explicit-id", explicit["submission_id"])
        generate_mock.assert_called_once_with("configs/baseline.yaml", "train")

    def test_ci_publishes_then_runs_cd_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            release = self._release_fixture(directory)
            ci_result = {
                "manifest_path": str(release.manifest_path),
                "release_id": "release-123",
            }
            cd_result = {"job_id": "ray-cats-dogs-release-123-check-config"}
            output = io.StringIO()
            with (
                patch(
                    "ray_cats_dogs.job_release.build_and_publish_release",
                    return_value=(release, ci_result),
                ) as publish_mock,
                patch(
                    "ray_cats_dogs.job_release.deploy_release_manifest",
                    return_value=cd_result,
                ) as deploy_mock,
                patch("sys.stdout", output),
            ):
                exit_code = ci_main([])

        self.assertEqual(0, exit_code)
        self.assertFalse(publish_mock.call_args.kwargs["dry_run"])
        self.assertEqual(release.manifest_path, deploy_mock.call_args.args[0])
        self.assertEqual("check-config", deploy_mock.call_args.kwargs["mode"])
        self.assertFalse(deploy_mock.call_args.kwargs["dry_run"])
        self.assertEqual(
            {"ci": ci_result, "cd": cd_result},
            json.loads(output.getvalue()),
        )

    def test_ci_can_stop_before_cd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            release = self._release_fixture(directory)
            ci_result = {
                "manifest_path": str(release.manifest_path),
                "release_id": "release-123",
            }
            output = io.StringIO()
            with (
                patch(
                    "ray_cats_dogs.job_release.build_and_publish_release",
                    return_value=(release, ci_result),
                ),
                patch(
                    "ray_cats_dogs.job_release.deploy_release_manifest",
                ) as deploy_mock,
                patch("sys.stdout", output),
            ):
                exit_code = ci_main(["--no-cd"])

        self.assertEqual(0, exit_code)
        deploy_mock.assert_not_called()
        self.assertEqual(
            {"ci": ci_result, "cd": None},
            json.loads(output.getvalue()),
        )

    def test_cd_reuses_matching_successful_submission(self) -> None:
        manifest = {
            "project": "ray-cats-and-dogs",
            "release_id": "release-123",
            "runtime_env": {
                "working_dir": "s3://training-data/release-123/working-dir.zip",
            },
            "files": {
                "working_dir": {"sha256": "working-dir-sha256"},
            },
        }
        entrypoint = (
            "python scripts/train.py --config configs/smoke.yaml --check-config"
        )
        client = Mock()
        client.list_jobs.return_value = [
            SimpleNamespace(
                submission_id="ray-cats-dogs-release-123-check-config",
                entrypoint=entrypoint,
                metadata={
                    "project": "ray-cats-and-dogs",
                    "release_id": "release-123",
                    "working_dir_sha256": "working-dir-sha256",
                },
                status=SimpleNamespace(value="SUCCEEDED"),
            )
        ]
        with patch(
            "ray.job_submission.JobSubmissionClient",
            return_value=client,
        ):
            result = submit_release(
                manifest,
                "http://127.0.0.1:8265",
                "ray-cats-dogs-release-123-check-config",
                entrypoint,
            )

        self.assertEqual(
            {
                "job_id": "ray-cats-dogs-release-123-check-config",
                "reused": True,
                "status": "SUCCEEDED",
            },
            result,
        )
        client.submit_job.assert_not_called()

    def test_cd_requires_new_id_after_failed_submission(self) -> None:
        manifest = {
            "project": "ray-cats-and-dogs",
            "release_id": "release-123",
            "runtime_env": {
                "working_dir": "s3://training-data/release-123/working-dir.zip",
            },
            "files": {
                "working_dir": {"sha256": "working-dir-sha256"},
            },
        }
        entrypoint = (
            "python scripts/train.py --config configs/smoke.yaml --check-config"
        )
        client = Mock()
        client.list_jobs.return_value = [
            SimpleNamespace(
                submission_id="ray-cats-dogs-release-123-check-config",
                entrypoint=entrypoint,
                metadata={
                    "project": "ray-cats-and-dogs",
                    "release_id": "release-123",
                    "working_dir_sha256": "working-dir-sha256",
                },
                status=SimpleNamespace(value="FAILED"),
            )
        ]
        with (
            patch(
                "ray.job_submission.JobSubmissionClient",
                return_value=client,
            ),
            self.assertRaisesRegex(RuntimeError, "new --submission-id"),
        ):
            submit_release(
                manifest,
                "http://127.0.0.1:8265",
                "ray-cats-dogs-release-123-check-config",
                entrypoint,
            )

        client.submit_job.assert_not_called()


if __name__ == "__main__":
    unittest.main()
