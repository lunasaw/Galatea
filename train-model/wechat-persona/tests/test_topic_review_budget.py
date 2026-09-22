from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from wechat_persona.topic_context import TopicContractError
from wechat_persona.topic_review_budget import ReviewBudget


class ReviewBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.policy = {'max_requests': 2, 'max_input_tokens': 100,
                       'max_output_tokens': 10, 'max_total_output_tokens': 20}
        self.budget = ReviewBudget(self.workspace, {'pilot': 'a'}, self.policy)

    def test_restart_retains_interrupted_reservations(self):
        self.budget.reserve('first', 20)
        resumed = ReviewBudget(self.workspace, {'pilot': 'a'}, self.policy)
        resumed.reserve('retry', 20)
        with self.assertRaisesRegex(TopicContractError, 'budget'):
            resumed.reserve('third', 20)
        self.assertEqual(resumed.usage()['requests_without_reported_usage'], 2)

    def test_provider_usage_above_estimate_stops_next_request(self):
        index = self.budget.reserve('first', 20)
        self.budget.settle(index, {'input_tokens': 95, 'output_tokens': 5})
        with self.assertRaisesRegex(TopicContractError, 'budget'):
            self.budget.reserve('second', 20)
        self.assertEqual(self.budget.usage()['input_tokens'], 95)

    def test_concurrent_reservations_cannot_exceed_request_budget(self):
        def reserve(index):
            try:
                self.budget.reserve(str(index), 10)
                return True
            except TopicContractError:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(reserve, range(8))), 2)
        self.assertEqual(self.budget.usage()['requests'], 2)

    def test_identity_and_invalid_usage_rejected(self):
        index = self.budget.reserve('first', 20)
        with self.assertRaisesRegex(TopicContractError, 'identity'):
            ReviewBudget(self.workspace, {'pilot': 'other'}, self.policy).usage()
        with self.assertRaisesRegex(TopicContractError, 'usage'):
            self.budget.settle(index, {'input_tokens': -1})


if __name__ == '__main__':
    unittest.main()
