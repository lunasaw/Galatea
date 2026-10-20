import unittest

from galatea_app.budgets import valid_limits, within_limits


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.finite = {
            "attempts": 1, "retries": 0, "wall_seconds": 600, "memory_bytes": 1024,
        }
        self.uncapped = {**self.finite, "memory_bytes": None}

    def test_unbounded_memory_requires_explicit_matching_policy(self):
        self.assertTrue(valid_limits(self.uncapped))
        self.assertFalse(within_limits(self.uncapped, self.finite))
        self.assertFalse(within_limits(
            self.uncapped, self.finite, allow_unbounded_memory=True))
        self.assertFalse(within_limits(self.uncapped, self.uncapped))
        self.assertTrue(within_limits(
            self.uncapped, self.uncapped, allow_unbounded_memory=True))
        self.assertFalse(within_limits(
            {**self.uncapped, "wall_seconds": 601}, self.uncapped,
            allow_unbounded_memory=True))

    def test_invalid_numbers_cannot_be_authorized(self):
        for field, value in [
            ("attempts", True), ("attempts", 1.5), ("retries", -1),
            ("wall_seconds", float("inf")), ("wall_seconds", float("nan")),
            ("memory_bytes", True), ("memory_bytes", 0), ("memory_bytes", "1024"),
        ]:
            with self.subTest(field=field, value=value):
                limits = {**self.finite, field: value}
                self.assertFalse(valid_limits(limits))
                self.assertFalse(within_limits(limits, self.finite))
                self.assertFalse(within_limits(self.finite, limits))
        self.assertTrue(within_limits(self.finite, self.finite))
