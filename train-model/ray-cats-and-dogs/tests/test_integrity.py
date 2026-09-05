from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_cats_dogs.config import load_config  # noqa: E402
from ray_cats_dogs.integrity import CONTEXT_IDENTITY_TUPLES, build_integrity_report  # noqa: E402


class IntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(PROJECT_ROOT / "configs" / "baseline.yaml")
        cls.dataset = SimpleNamespace(
            split_frame=lambda name: pd.DataFrame(
                {
                    "relative_path": {"training": ["PetImages/Cat/a.jpg"], "validation": ["PetImages/Dog/b.jpg"], "test": ["PetImages/Cat/c.jpg"]}[name],
                    "sha256": {"training": ["hash-a"], "validation": ["hash-b"], "test": ["hash-c"]}[name],
                }
            )
        )

    def test_report_is_deterministic_and_declares_all_contexts(self) -> None:
        first = build_integrity_report(self.config, self.dataset, include_test=False)
        second = build_integrity_report(self.config, self.dataset, include_test=False)
        self.assertEqual(first, second)
        self.assertEqual("passed", first["preprocessing"]["parity"]["status"])
        self.assertEqual(set(CONTEXT_IDENTITY_TUPLES), set(first["preprocessing"]["context_identity_tuples"]))
        self.assertEqual(64, len(first["integrity_digest"]))
        self.assertEqual(first["preprocessing_digest"], first["preprocessing"]["digest"])

    def test_trial_does_not_read_test_content(self) -> None:
        report = build_integrity_report(self.config, self.dataset, include_test=False)
        boundaries = report["migration"]["contamination"]["split_boundaries"]
        self.assertEqual(["training", "validation"], boundaries["checked_splits"])
        self.assertFalse(boundaries["test_metadata_used"])
        self.assertFalse(boundaries["test_use"]["content_read"])
        self.assertFalse(report["preprocessing"]["test_content_read"])

    def test_split_grouping_keeps_duplicate_content_in_one_split(self) -> None:
        from ray_cats_dogs.data import _assign_grouped_splits

        records = [
            {"sha256": "same", "label": 0},
            {"sha256": "same", "label": 0},
            {"sha256": "other", "label": 0},
            {"sha256": "other2", "label": 0},
            {"sha256": "other3", "label": 0},
            {"sha256": "other4", "label": 0},
            {"sha256": "other5", "label": 0},
            {"sha256": "other6", "label": 0},
            {"sha256": "other7", "label": 0},
            {"sha256": "other8", "label": 0},
            {"sha256": "other9", "label": 0},
            {"sha256": "other10", "label": 0},
            {"sha256": "other11", "label": 0},
            {"sha256": "other12", "label": 0},
            {"sha256": "other13", "label": 0},
            {"sha256": "other14", "label": 0},
            {"sha256": "other15", "label": 0},
            {"sha256": "other16", "label": 0},
            {"sha256": "other17", "label": 0},
            {"sha256": "other18", "label": 0},
            {"sha256": "other19", "label": 0},
        ]
        _assign_grouped_splits(records, self.config.data, seed=42)
        self.assertEqual({records[0]["split"]}, {records[1]["split"]})

    def test_champion_can_check_test_metadata(self) -> None:
        report = build_integrity_report(self.config, self.dataset, include_test=True)
        boundaries = report["migration"]["contamination"]["split_boundaries"]
        self.assertEqual(["training", "validation", "test"], boundaries["checked_splits"])
        self.assertTrue(boundaries["test_metadata_used"])


if __name__ == "__main__":
    unittest.main()
