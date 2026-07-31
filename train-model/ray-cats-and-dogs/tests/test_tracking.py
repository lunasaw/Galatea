from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_cats_dogs.tracking import RayMlflowCallback  # noqa: E402


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
                "worker_rank": 0,
                "world_size": 2,
                "val_accuracy": 0.8,
            },
            {
                "epoch": 2,
                "worker_rank": 1,
                "world_size": 2,
                "val_accuracy": 0.1,
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


if __name__ == "__main__":
    unittest.main()
