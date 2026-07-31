from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_cats_dogs.worker import _is_better  # noqa: E402


class ObjectiveTest(unittest.TestCase):
    def test_objective_direction_is_explicit(self) -> None:
        self.assertTrue(_is_better(0.9, 0.8, "max"))
        self.assertFalse(_is_better(0.7, 0.8, "max"))
        self.assertTrue(_is_better(0.2, 0.3, "min"))
        self.assertFalse(_is_better(0.4, 0.3, "min"))


if __name__ == "__main__":
    unittest.main()
