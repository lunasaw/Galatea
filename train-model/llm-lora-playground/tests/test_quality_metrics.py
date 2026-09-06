import unittest

from llm_lora_playground.quality import _f1, _input_ids, _quality_score, _repeated_trigram, _rouge_l


class QualityMetricTests(unittest.TestCase):
    def test_overlap_metrics_are_bounded_and_exact_is_one(self):
        tokens = [1, 2, 3, 4]
        self.assertEqual(1.0, _f1(tokens, tokens))
        self.assertEqual(1.0, _rouge_l(tokens, tokens))

    def test_repetition_and_score_penalties_are_explicit(self):
        self.assertTrue(_repeated_trigram([1, 2, 3, 1, 2, 3]))
        clean = _quality_score({
            "token_f1_mean": 0.5,
            "rouge_l_f1_mean": 0.5,
            "exact_match_rate": 0.0,
            "format_follow_rate": 1.0,
            "repetition_3gram_rate": 0.0,
            "max_length_stop_rate": 0.0,
        })
        repeated = _quality_score({
            "token_f1_mean": 0.5,
            "rouge_l_f1_mean": 0.5,
            "exact_match_rate": 0.0,
            "format_follow_rate": 1.0,
            "repetition_3gram_rate": 1.0,
            "max_length_stop_rate": 0.0,
        })
        self.assertGreater(clean, repeated)

    def test_batch_encoding_like_mapping_yields_integer_input_ids(self):
        class MappingLike:
            def get(self, key):
                return [1, 2, 3] if key == "input_ids" else None

        self.assertEqual([1, 2, 3], _input_ids(MappingLike()))
        with self.assertRaises(ValueError):
            _input_ids("rendered prompt")


if __name__ == "__main__":
    unittest.main()
