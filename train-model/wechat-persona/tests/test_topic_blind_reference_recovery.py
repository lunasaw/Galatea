from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_topic_blind_reference as fixtures
from wechat_persona import topic_blind_reference_recovery as recovery
from wechat_persona.topic_candidates import read_json
from wechat_persona.topic_context import TopicContractError


class ReferenceIndexRecoveryTests(unittest.TestCase):
    def provider(self, request, **kwargs):
        payload = json.loads(request.data)
        data = json.loads(payload['messages'][1]['content'])
        allowed = data['selected']['allowed_responds_to_indices']
        self.assertTrue(all(data['selected']['past_messages'][i]['speaker'] == 'self' for i in allowed))
        label = fixtures.label_for(data)
        label.update(responds_to_indices=[allowed[-1]], required_context_indices=[allowed[-1]])
        return io.BytesIO(json.dumps({'model': payload['model'], 'usage': {'prompt_tokens': 500, 'completion_tokens': 100},
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(label)}}]}).encode())

    def test_annotations_preserve_original_evidence_and_hide_labels(self):
        for case in recovery.fixtures():
            original = deepcopy(case)
            annotated = recovery.explicit_indices(case)
            self.assertEqual(case, original)
            self.assertEqual(annotated['candidate_sha256'], original['candidate_sha256'])
            self.assertEqual(annotated['selected']['case']['past_messages'], original['selected']['case']['past_messages'])
            self.assertEqual(annotated['selected']['case']['reply'], original['selected']['case']['reply'])
            self.assertEqual(annotated['selected']['source_ids'], original['selected']['source_ids'])
            self.assertNotIn('machine_label', json.dumps(recovery.core.make_input(annotated, 'selected')))

    def test_multiturn_preflight_and_offline_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = fixtures.BlindReferenceTests().args(Path(tmp))
            args['config_path'] = fixtures.ROOT / 'configs/topic-blind-reference-index-recovery-v1.yaml'
            with patch('wechat_persona.topic_validation_completion.urlopen', side_effect=self.provider) as network:
                result = recovery.run_reference(**args, execute=True)
                self.assertEqual(result['completed_references'], 6)
                self.assertTrue(result['route_gate_passed'])
                self.assertEqual(network.call_count, 12)
            with patch('wechat_persona.topic_validation_completion.urlopen', side_effect=AssertionError('network forbidden')):
                self.assertEqual(recovery.run_reference(**args, execute=True), result)
            directory = Path(result['workspace'])
            config = recovery.load_config(args['config_path'])
            config.update(budget=config['preflight_budget'], max_repair_requests=0)
            references, journal = recovery.replay_rows(directory, recovery.fixtures(), config)
            self.assertEqual(len(references), 6)
            self.assertEqual(len(journal.seen), 12)
            (directory / 'references.json').write_text('{}')
            with self.assertRaises(TopicContractError):
                recovery.replay_rows(directory, recovery.fixtures(), config)


if __name__ == '__main__':
    unittest.main()
