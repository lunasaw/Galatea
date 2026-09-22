import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from wechat_persona.topic_context import TopicContractError
from wechat_persona.topic_review import candidate_strata, parse_response
from wechat_persona.topic_candidates import load_policy
from wechat_persona.topic_context import Turn
import test_topic_candidates as fixtures


class TopicReviewTests(unittest.TestCase):
    def result(self, **overrides):
        return {'index': 0, 'status': 'keep', 'reason': 'usable_reply', 'confidence': 0.9,
                'reply_link_correct': True, 'context_complete': True,
                'topic_category': 'work', **overrides}

    def parse(self, results, count=1):
        return parse_response({'output_text': json.dumps({'results': results})}, count)

    def test_incomplete_duplicate_and_out_of_range_results_fail(self):
        for rows in ([], [self.result(),self.result()], [self.result(index=1)]):
            with self.assertRaises(TopicContractError):
                self.parse(rows)

    def test_claimed_keep_with_incomplete_context_is_downgraded(self):
        self.assertEqual(self.parse([self.result(context_complete=False)])[0]['status'],'uncertain')
        self.assertEqual(self.parse([self.result(confidence=0.5)])[0]['status'],'uncertain')

    def test_model_cannot_replace_reply_or_return_free_text(self):
        with self.assertRaisesRegex(TopicContractError,'invalid_machine_review_response'):
            self.parse([self.result(rewritten_reply='invented reply')])

    def test_valid_response_retains_machine_judgments(self):
        self.assertEqual(self.parse([self.result()]),[self.result()])

    def test_multiple_categories_and_topic_return_are_distinct_prefix_proxies(self):
        fixture = fixtures.TopicCandidateTests()
        fixture.setUp()
        fixture.config = load_policy(ROOT / 'configs/daily-topic-sft-v2.yaml')
        prefix = [Turn((fixtures.message(i, role, text),)) for i, (role, text) in enumerate([
            ('self', '工作项目开会'), ('target', '讨论工作项目'),
            ('self', '晚饭吃什么'), ('target', '晚饭吃面条'), ('self', '工作项目进展呢'),
        ])]
        row = fixture.candidate(prefix=prefix, target=Turn((fixtures.message(5, 'target', '继续整理'),)))
        strata = candidate_strata(row)
        self.assertIn('multiple_context_categories', strata)
        self.assertIn('context_topic_return', strata)
        self.assertNotIn('explicit_reference', strata)

    def test_unresolved_category_alone_is_not_a_topic_return(self):
        fixture = fixtures.TopicCandidateTests()
        fixture.setUp()
        row = fixture.candidate(prefix=[Turn((fixtures.message(0, 'self', '工作项目和晚饭'),))])
        strata = candidate_strata(row)
        self.assertIn('unresolved_category', strata)
        self.assertNotIn('context_topic_return', strata)
        self.assertNotIn('multiple_context_categories', strata)


if __name__=='__main__':
    unittest.main()
