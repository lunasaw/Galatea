from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from wechat_persona.topic_adjudication import load_fixtures, load_policy
from wechat_persona.topic_context import TopicContractError
from wechat_persona.topic_cross_review import load_config, wire_payload
from wechat_persona.topic_rubric_calibration import agreement_report, fixture_views, run_calibration


class RubricCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.policy_path = ROOT / 'configs/topic-adjudication-v1.yaml'
        _, self.fixtures = load_fixtures(self.policy_path, load_policy(self.policy_path))

    def test_reviewer_never_sees_expected_labels_or_fixture_identity(self):
        policy = load_config(ROOT / 'configs/topic-cross-review-v1.yaml')
        views = fixture_views(self.fixtures)
        for judge in policy['judges'].values():
            wire = wire_payload(views[:6], policy, judge)
            content = wire.get('messages', wire.get('input'))[1]['content']
            self.assertNotIn('expected', content)
            for row in self.fixtures:
                self.assertNotIn(row['id'], content)
            self.assertEqual(set(json.loads(content)['cases'][0]), {'index', 'reply', 'past_messages'})

    def test_incomplete_judge_keeps_original_denominator(self):
        report = agreement_report(self.fixtures, [])
        for row in report['judges'].values():
            self.assertEqual(row['population'], 18)
            self.assertEqual(row['reviewed'], 0)
            self.assertEqual(row['all_core_axes_agree'], 0)
        self.assertFalse(report['true_precision_claimed'])

    def test_duplicate_judgments_fail_closed(self):
        f = self.fixtures[0]
        row = {**f['expected'], 'confidence': .95, 'sample_id': f['id'], 'judge': 'independent'}
        with self.assertRaisesRegex(TopicContractError, 'duplicate'):
            agreement_report(self.fixtures, [row, row])

    def test_complete_run_replays_without_calls_or_reinterpreting_private_labels(self, malformed=False):
        by_reply = {r['reply']: r for r in self.fixtures}
        def respond(request, **kwargs):
            value = json.loads(request.data)
            cases = json.loads(value.get('messages', value.get('input'))[1]['content'])['cases']
            labels = [{'index': i, 'confidence': .95, **by_reply[c['reply']]['expected']} for i, c in enumerate(cases)]
            text = json.dumps({'results': labels})
            if malformed and value['model'].startswith('claude') and cases[0]['reply'] == self.fixtures[0]['reply']:
                text = '{}'
            result = {'model': value['model'], 'output_text': text, 'status': 'completed',
                      'usage': {'input_tokens': 100, 'output_tokens': 100},
                      'choices': [{'finish_reason': 'stop', 'message': {'content': text}}]}
            class Response:
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self): return json.dumps(result).encode()
            return Response()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = root / 'auth.json'
            auth.write_text(json.dumps({'OPENAI_API_KEY': 'synthetic-credential'}))
            kwargs = dict(policy_path=self.policy_path, cross_policy_path=ROOT / 'configs/topic-cross-review-v1.yaml',
                          output_root=root / 'output', controlled_root=root, base_url='https://example.invalid/',
                          auth_file=auth, authorization_reference='synthetic-test')
            with patch('wechat_persona.topic_rubric_calibration.urlopen', side_effect=respond) as network:
                plan = run_calibration(**kwargs)
                self.assertFalse(Path(plan['workspace']).exists())
                self.assertEqual(network.call_count, 0)
                first = run_calibration(**kwargs, execute=True)
                second = run_calibration(**kwargs, execute=True)
                self.assertEqual(first, second)
                self.assertEqual(network.call_count, 6)
                self.assertEqual(first['usage']['requests'], 6)
                self.assertEqual(first['status'], 'incomplete' if malformed else 'complete')
                self.assertFalse(first['training_run'])
                self.assertEqual(first['private_samples_reviewed'], 0)
                if not malformed:
                    for row in first['judges'].values():
                        self.assertEqual(row['all_core_axes_agree'], 18)
                    (Path(first['workspace']) / 'report.json').write_text('{}')
                    with self.assertRaises(TopicContractError):
                        run_calibration(**kwargs, execute=True)
                    self.assertEqual(network.call_count, 6)

    def test_invalid_response_is_retained_without_automatic_requery(self):
        self.test_complete_run_replays_without_calls_or_reinterpreting_private_labels(malformed=True)


if __name__ == '__main__':
    unittest.main()
