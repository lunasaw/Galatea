import unittest

from llm_lora_playground.sft import LossMaskContractError, build_assistant_only_labels


class LossMaskTests(unittest.TestCase):
    def test_only_assistant_spans_are_supervised(self):
        labels = build_assistant_only_labels([1, 2, 3, 4, 5, 0], [(2, 5)], 0)
        self.assertEqual(labels, [-100, -100, 3, 4, 5, -100])

    def test_no_assistant_span_is_a_hard_failure(self):
        with self.assertRaises(LossMaskContractError):
            build_assistant_only_labels([1, 2], [], 0)

    def test_out_of_range_span_is_rejected(self):
        with self.assertRaises(LossMaskContractError):
            build_assistant_only_labels([1, 2], [(1, 8)], 0)


if __name__ == "__main__":
    unittest.main()
