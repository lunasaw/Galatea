from __future__ import annotations

import hashlib
import json
import multiprocessing
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_cats_dogs.config import load_config
from ray_cats_dogs.data import _digest_manifest, _persist_manifest, load_frozen_manifest
from ray_cats_dogs.evaluate import evaluate_checkpoint
from ray_cats_dogs.finalization import (
    FINAL_PROTOCOL, FROZEN_ARTIFACTS, canonical, claim_test_once,
    download_frozen_artifacts, validate_candidate_config, validate_freeze,
)


def compete(root, binding, barrier, queue):
    barrier.wait(timeout=20)
    try:
        claim_test_once(Path(root), binding)
        queue.put("acquired")
    except RuntimeError:
        queue.put("rejected")


class FinalizationTest(unittest.TestCase):
    def test_artifact_recovery_accepts_flat_download_return_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flat = root / "flat"
            flat.mkdir()
            payloads = {name: name.encode() for name in FROZEN_ARTIFACTS}
            digests = {
                name: "sha256:" + hashlib.sha256(payload).hexdigest()
                for name, payload in payloads.items()
            }

            def download(*, run_id, artifact_path, dst_path):
                path = flat / Path(artifact_path).name
                path.write_bytes(payloads[artifact_path])
                return str(path)

            with patch("mlflow.artifacts.download_artifacts", side_effect=download):
                paths = download_frozen_artifacts("trial", digests, root / "recovered")
                for artifact, path in paths.items():
                    self.assertEqual(path.read_bytes(), payloads[artifact])
                payloads["checkpoints/best/best-model.pt"] = b"tampered"
                with self.assertRaisesRegex(RuntimeError, "Artifact changed"):
                    download_frozen_artifacts("trial", digests, root / "tampered")

    def binding(self, submission="submission-a"):
        return {
            "checkpoint_sha256": "a" * 64,
            "split_sha256": "b" * 64,
            "protocol": FINAL_PROTOCOL,
            "submission_id": submission,
            "execution_identity": "sha256:" + "c" * 64,
            "candidate_run_id": "trial",
            "run_id": "champion",
        }

    def test_actual_process_race_and_changed_submission_cannot_reclaim(self):
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as directory:
            barrier = context.Barrier(6)
            queue = context.Queue()
            processes = [
                context.Process(
                    target=compete,
                    args=(directory, self.binding(f"submission-{index}"), barrier, queue),
                )
                for index in range(6)
            ]
            for process in processes:
                process.start()
            results = [queue.get(timeout=40) for _ in processes]
            for process in processes:
                process.join(timeout=20)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(results.count("acquired"), 1)
            markers = list((Path(directory) / "test-uses").glob("*.json"))
            self.assertEqual(len(markers), 1)
            original = json.loads(markers[0].read_bytes())
            for submission in (original["submission_id"], "new-submission-after-restart"):
                with self.assertRaisesRegex(RuntimeError, "already claimed"):
                    claim_test_once(Path(directory), self.binding(submission))
            # A crash/corruption cannot reset the exclusive reservation.
            markers[0].write_bytes(b"")
            with self.assertRaisesRegex(RuntimeError, "already claimed"):
                claim_test_once(Path(directory), self.binding())

    def test_evaluation_rejects_missing_claim_and_changed_checkpoint_before_test_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "best-model.pt").write_bytes(b"fixture, not trained weights")
            checkpoint = Mock()
            checkpoint.as_directory.return_value.__enter__ = Mock(return_value=directory)
            checkpoint.as_directory.return_value.__exit__ = Mock(return_value=False)
            with patch("ray_cats_dogs.input_pipeline.make_local_dataset") as loader:
                with self.assertRaisesRegex(RuntimeError, "test-once"):
                    evaluate_checkpoint(checkpoint, [], directory, (150, 150), 128)
                with self.assertRaisesRegex(RuntimeError, "differs from frozen"):
                    evaluate_checkpoint(checkpoint, [], directory, (150, 150), 128,
                                        str(path / "state"), self.binding())
                loader.assert_not_called()
                self.assertFalse((path / "state").exists())

    def test_existing_claim_rejects_worker_before_sample_access(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "best-model.pt").write_bytes(b"fixture")
            binding = {**self.binding(), "checkpoint_sha256": hashlib.sha256(b"fixture").hexdigest()}
            claim_test_once(path / "state", binding)
            checkpoint = Mock()
            checkpoint.as_directory.return_value.__enter__ = Mock(return_value=directory)
            checkpoint.as_directory.return_value.__exit__ = Mock(return_value=False)
            with patch("ray_cats_dogs.input_pipeline.make_local_dataset") as loader:
                with self.assertRaisesRegex(RuntimeError, "already claimed"):
                    evaluate_checkpoint(
                        checkpoint, [{"relative_path": "must-not-open.jpg"}],
                        directory, (150, 150), 128, str(path / "state"), binding,
                    )
                loader.assert_not_called()

    def test_final_manifest_load_never_opens_images(self):
        config = load_config(PROJECT_ROOT / "configs/champion.yaml")
        records = [
            {"relative_path": f"{name}/{split}.jpg", "class_name": name,
             "label": label, "bytes": 1, "sha256": str(label) * 64, "split": split}
            for name, label in (("Cat", 0), ("Dog", 1))
            for split in ("training", "validation", "test")
        ]
        frame = pd.DataFrame(records)
        content, split = _digest_manifest(frame)
        with tempfile.TemporaryDirectory() as directory:
            path = _persist_manifest(frame, Path(directory), content, split)
            config = replace(
                config, data=replace(config.data, expected_valid_images=6),
                evaluation=replace(config.evaluation, manifest_path=path),
            )
            with patch("PIL.Image.open", side_effect=AssertionError("test image read")):
                dataset = load_frozen_manifest(config)
            self.assertEqual(dataset.split_digest, split)
            self.assertEqual(dataset.content_digest, content)

    def test_frozen_config_rejects_hyperparameter_seed_and_topology_changes(self):
        config = load_config(PROJECT_ROOT / "configs/champion.yaml")
        candidate = config.as_dict()
        validate_candidate_config(candidate, config)
        for changed in (
            replace(config, training=replace(config.training, learning_rate=0.01)),
            replace(config, run=replace(config.run, seed=43)),
            replace(config, ray=replace(config.ray, num_workers=2)),
        ):
            with self.assertRaises(RuntimeError):
                validate_candidate_config(candidate, changed)

    def test_freeze_requires_actual_submission_and_candidate_evidence_binding(self):
        config = load_config(PROJECT_ROOT / "configs/champion.yaml")
        dataset = SimpleNamespace(split_digest="b" * 64, content_digest="d" * 64)
        identity = {
            "readinessIdentity": "sha256:" + "1" * 64,
            "candidateRunId": "trial",
            "candidateEvidenceDigest": "sha256:" + "2" * 64,
        }
        execution = "sha256:" + hashlib.sha256(canonical(identity)).hexdigest()
        provenance = {
            "galatea.release.id": "release",
            "galatea.submission.id": "submission",
            "galatea.execution.identity": execution,
            "galatea.readiness.digest": "ready",
            "candidate_run_id": "trial",
            "candidate_evidence_digest": identity["candidateEvidenceDigest"],
        }
        freeze = {
            "schema_version": "ray-cats-dogs/candidate-freeze/v1",
            "project": config.project_name, "release_id": "release",
            "submission_id": "submission", "execution_identity": execution,
            "readiness_digest": "ready", "candidate_run_id": "trial",
            "candidate_evidence_digest": identity["candidateEvidenceDigest"],
            "readiness_identity": identity["readinessIdentity"],
            "config_digest": config.config_digest,
            "split_sha256": dataset.split_digest, "content_sha256": dataset.content_digest,
            "checkpoint_sha256": "a" * 64, "protocol": FINAL_PROTOCOL,
        }
        validate_freeze(freeze, config, dataset, provenance)
        for field in ("submission_id", "checkpoint_sha256", "split_sha256", "candidate_evidence_digest"):
            with self.assertRaises(RuntimeError):
                validate_freeze({**freeze, field: "changed"}, config, dataset, provenance)

    def test_ray_evaluation_disables_transparent_task_retry(self):
        from ray_cats_dogs.train import _run_test_evaluation

        config = load_config(PROJECT_ROOT / "configs/champion.yaml")
        ray = Mock()
        ray.get_runtime_context.return_value.runtime_env = {"py_modules": ["fixture.whl"]}
        dataset = Mock()
        dataset.split_frame.return_value = pd.DataFrame(
            [{"relative_path": "unused.jpg", "label": 0, "sha256": "a" * 64}]
        )
        freeze = {
            **self.binding(), "candidate_evidence_digest": "evidence",
            "readiness_digest": "ready", "release_id": "release",
        }
        _run_test_evaluation(ray, config, dataset, Mock(), freeze, "run")
        self.assertEqual(ray.remote.return_value.options.call_args.kwargs["max_retries"], 0)
