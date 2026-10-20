from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_cats_dogs.config import load_config  # noqa: E402
from ray_cats_dogs.data import (  # noqa: E402
    _validated_class_records,
    prepare_dataset,
    prepare_run_dataset,
    validate_equal_shards,
)


class DatasetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        for class_name, channel in (("Cat", 0), ("Dog", 1)):
            class_dir = self.root / "dataset" / "PetImages" / class_name
            class_dir.mkdir(parents=True)
            for index in range(20):
                color = [0, 0, 0]
                color[channel] = index * 10
                Image.new("RGB", (12, 12), tuple(color)).save(
                    class_dir / f"{index:03d}.jpg"
                )
            (class_dir / "corrupt.jpg").write_bytes(b"not-an-image")
        with patch.dict(os.environ, {}, clear=True):
            baseline = load_config(PROJECT_ROOT / "configs" / "baseline.yaml")
        self.config = replace(
            baseline.data,
            root=self.root / "dataset",
            cache_dir=self.root / "cache",
            expected_images_per_class=21,
            expected_valid_images=40,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_manifest_is_deterministic_and_keeps_source_read_only(self) -> None:
        first = prepare_dataset(self.config, seed=42)
        second = prepare_dataset(self.config, seed=42)

        self.assertEqual(first.content_digest, second.content_digest)
        self.assertEqual(first.split_digest, second.split_digest)
        self.assertEqual(first.manifest_path, second.manifest_path)
        self.assertEqual(2, len(first.invalid_files))
        self.assertNotIn("path", first.manifest.columns)
        counts = first.manifest.groupby(["split", "class_name"]).size()
        self.assertEqual(18, counts.loc[("training", "Cat")])
        self.assertEqual(1, counts.loc[("validation", "Dog")])
        self.assertEqual(1, counts.loc[("test", "Cat")])
        self.assertTrue(
            (self.root / "dataset" / "PetImages" / "Cat" / "corrupt.jpg").exists()
        )

    def test_seed_changes_split_but_not_content_identity(self) -> None:
        first = prepare_dataset(self.config, seed=42)
        second = prepare_dataset(self.config, seed=43)

        self.assertEqual(first.content_digest, second.content_digest)
        self.assertNotEqual(first.split_digest, second.split_digest)

    def test_equal_shards_rejects_silent_ray_row_drops(self) -> None:
        dataset = prepare_dataset(self.config, seed=42)

        validate_equal_shards(dataset, num_workers=2)
        with self.assertRaisesRegex(ValueError, "divide evenly"):
            validate_equal_shards(dataset, num_workers=3)

    def test_decode_warning_marks_image_invalid(self) -> None:
        pet_images_root = self.root / "warning-dataset" / "PetImages"
        image_path = pet_images_root / "Cat" / "warning.jpg"
        image_path.parent.mkdir(parents=True)
        image_path.write_bytes(b"placeholder")
        with patch("ray_cats_dogs.data.Image.open") as open_image:
            image = open_image.return_value.__enter__.return_value
            image.load.side_effect = UserWarning("Truncated File Read")
            valid, invalid, source_count = _validated_class_records(
                pet_images_root,
                "Cat",
                0,
                self.config,
                seed=42,
            )

        self.assertEqual(1, source_count)
        self.assertEqual([], valid)
        self.assertEqual(
            [{"relative_path": "Cat/warning.jpg", "error_type": "UserWarning"}],
            invalid,
        )

    def test_frozen_training_rejects_changed_bytes_without_reading_test_images(self) -> None:
        frozen = prepare_dataset(self.config, seed=42)
        config = load_config(PROJECT_ROOT / "configs" / "baseline.yaml")
        config = replace(
            config,
            data=self.config,
            evaluation=replace(config.evaluation, manifest_path=frozen.manifest_path),
        )
        # Remove test fixtures: successful preparation proves no test bytes are read.
        for row in frozen.split_frame("test").to_dict(orient="records"):
            (self.config.root / "PetImages" / row["relative_path"]).unlink()
        with patch("PIL.Image.open", side_effect=AssertionError("unexpected decode")):
            actual = prepare_run_dataset(config)
        self.assertEqual(frozen.split_digest, actual.split_digest)
        row = frozen.split_frame("training").iloc[0]
        (self.config.root / "PetImages" / row["relative_path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "differs from frozen manifest"):
            prepare_run_dataset(config)


if __name__ == "__main__":
    unittest.main()
