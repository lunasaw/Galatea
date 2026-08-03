from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_cats_dogs.tracking import (  # noqa: E402
    RAY_TASK_TIMELINE_ARTIFACT_PATH,
    RAY_TASK_TIMELINE_LIMIT,
    RAY_TASK_TIMELINE_METADATA_PATH,
    RayMlflowCallback,
    log_ray_task_timeline,
)


class FakeClient:
    def __init__(self) -> None:
        self.metrics = []
        self.tags = []

    def log_batch(self, run_id, metrics, synchronous):
        self.metrics.append((run_id, metrics, synchronous))

    def set_tag(self, run_id, key, value, synchronous):
        self.tags.append((run_id, key, value, synchronous))


class RayMlflowCallbackTest(unittest.TestCase):
    def test_only_rank_zero_report_is_logged_to_existing_run(self) -> None:
        callback = RayMlflowCallback("http://tracking", "run-123")
        client = FakeClient()
        reports = [
            {
                "epoch": 2,
                "worker_rank": 1,
                "world_size": 2,
                "val_accuracy": 0.1,
            },
            {
                "epoch": 2,
                "worker_rank": 0,
                "world_size": 2,
                "val_accuracy": 0.8,
            },
        ]

        with patch.object(callback, "_client", return_value=client):
            callback.after_report(None, reports, SimpleNamespace(path="s3://checkpoint"))

        self.assertEqual(1, len(client.metrics))
        run_id, metrics, synchronous = client.metrics[0]
        self.assertEqual("run-123", run_id)
        self.assertTrue(synchronous)
        values = {metric.key: metric.value for metric in metrics}
        self.assertEqual(0.8, values["val_accuracy"])
        self.assertNotIn("worker_rank", values)
        self.assertEqual(
            ("run-123", "ray.latest_checkpoint_uri", "s3://checkpoint", True),
            client.tags[0],
        )


class RayTaskTimelineTest(unittest.TestCase):
    def test_current_job_timeline_is_logged_and_verified(self) -> None:
        timeline_json = json.dumps(
            [
                {
                    "name": "task:execute",
                    "ph": "X",
                    "pid": 0,
                    "tid": 0,
                    "ts": 1000,
                    "dur": 250,
                },
                {
                    "name": "process_name",
                    "ph": "M",
                    "pid": 0,
                    "args": {"name": "Node 10.0.0.1"},
                },
            ]
        )
        tasks = [{"task_id": "task-1"}, {"task_id": "task-2"}]

        with tempfile.TemporaryDirectory() as directory:
            downloaded = Path(directory) / "task-timeline.json"
            downloaded.write_text(timeline_json, encoding="utf-8")
            with (
                patch(
                    "ray_cats_dogs.tracking.list_tasks", return_value=tasks
                ) as list_tasks_mock,
                patch(
                    "ray_cats_dogs.tracking.chrome_tracing_dump",
                    return_value=timeline_json,
                ),
                patch("ray_cats_dogs.tracking.mlflow.log_text") as log_text,
                patch("ray_cats_dogs.tracking.mlflow.log_dict") as log_dict,
                patch("ray_cats_dogs.tracking.mlflow.set_tags") as set_tags,
                patch(
                    "ray_cats_dogs.tracking.mlflow.artifacts.download_artifacts",
                    return_value=str(downloaded),
                ) as download_artifacts,
            ):
                metadata = log_ray_task_timeline("run-123", "job-456")

        list_tasks_mock.assert_called_once_with(
            filters=[("job_id", "=", "job-456")],
            limit=RAY_TASK_TIMELINE_LIMIT,
            detail=True,
            raise_on_missing_output=True,
        )
        log_text.assert_called_once_with(
            timeline_json,
            RAY_TASK_TIMELINE_ARTIFACT_PATH,
            run_id="run-123",
        )
        download_artifacts.assert_called_once()
        log_dict.assert_called_once_with(
            metadata,
            RAY_TASK_TIMELINE_METADATA_PATH,
            run_id="run-123",
        )
        self.assertEqual(2, metadata["task_count"])
        self.assertEqual(1, metadata["complete_event_count"])
        self.assertEqual(
            hashlib.sha256(timeline_json.encode()).hexdigest(),
            metadata["sha256"],
        )
        self.assertEqual(
            "true", set_tags.call_args.args[0]["ray.task_timeline.logged"]
        )


if __name__ == "__main__":
    unittest.main()
