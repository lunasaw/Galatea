from __future__ import annotations

import copy
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from wechat_persona.topic_candidates import load_policy
from wechat_persona.topic_context import TopicContractError
from wechat_persona.topic_evidence_audit import (
    build_views, evidence_view, load_config, parse_response, payload, run_audit, summarize,
)
import test_topic_candidates as fixtures


class EvidenceAuditTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TopicCandidateTests()
        self.fixture.setUp()
        self.fixture.config = load_policy(ROOT / 'configs/daily-topic-sft-v2.yaml')
        self.row = self.fixture.candidate()
        self.policy = load_config(ROOT / 'configs/topic-evidence-audit-v1.yaml')
        self.view = evidence_view(self.row, self.row['context_messages'], 'selected')
        self.label = {'index': 0, 'status': 'keep', 'reason': 'usable_reply', 'confidence': .95,
                      'reply_link_correct': True, 'context_complete': True,
                      'responds_to_indices': [2], 'required_context_indices': [0, 1, 2]}
        self.previous = {self.row['sample_id']: {'status': 'keep', 'reply_link_correct': True, 'context_complete': True}}

    def parse(self, label=None, view=None):
        return parse_response({'output_text': json.dumps({'results': [label or self.label]})},
                              [view or self.view], self.policy['minimum_confidence'])

    def test_wire_payload_hides_previous_labels_ids_and_diagnostic_kind(self):
        sent = payload([{**self.view, 'old_status': 'reject', 'kind': 'expanded_diagnostic'}], self.policy)
        wire = json.loads(sent['input'][1]['content'])
        self.assertEqual(set(wire['cases'][0]), {'index', 'past_messages', 'reply'})
        serialized = json.dumps(wire)
        for forbidden in ('old_status', 'expanded_diagnostic', self.row['sample_id'], 'candidate_sha256'):
            self.assertNotIn(forbidden, serialized)

    def test_future_cross_split_and_changed_source_order_fail_closed(self):
        for changes in ({'split': 'test'}, {'session_id': 'other'}, {'order': 4},
                        {'timestamp': self.row['cutoff']['timestamp']}, {'message_id': self.row['target_message_ids'][0]}):
            with self.subTest(changes=changes), self.assertRaises(TopicContractError):
                messages = copy.deepcopy(self.row['context_messages'])
                messages[0].update(changes)
                evidence_view(self.row, messages, 'selected')

    def test_reply_link_requires_real_self_evidence_and_no_free_text(self):
        for changes in ({'responds_to_indices': [999]}, {'responds_to_indices': [1]},
                        {'responds_to_indices': []}, {'required_context_indices': [0]},
                        {'responds_to_indices': [2, 2]}, {'rewritten_reply': 'invented'}):
            with self.subTest(changes=changes), self.assertRaises(TopicContractError):
                self.parse({**self.label, **changes})

    def test_weak_keep_is_downgraded_and_valid_ids_map_back_to_sources(self):
        decision = self.parse()[0]
        self.assertEqual(decision['responds_to_ids'], ['m2'])
        self.assertEqual(decision['required_context_ids'], ['m0', 'm1', 'm2'])
        for changes in ({'confidence': .89}, {'context_complete': False}, {'reason': 'privacy'}):
            self.assertEqual(self.parse({**self.label, **changes})[0]['status'], 'uncertain')

    def test_expanded_view_cannot_rescue_current_candidate_into_consensus(self):
        current = self.parse({**self.label, 'status': 'uncertain', 'context_complete': False})[0]
        expanded = {**self.parse()[0], 'view_kind': 'expanded_diagnostic', 'required_context_ids': ['m0', 'm2', 'missing-from-selected']}
        previous = {self.row['sample_id']: {'status': 'uncertain', 'reply_link_correct': True, 'context_complete': False}}
        result = summarize([self.row], previous, [current, expanded], [])
        self.assertEqual(result['consensus_keep_count'], 0)
        self.assertEqual(result['diagnosis_counts'], {'machine_suggested_context_omission': 1})
        self.assertFalse(result['expanded_evidence_used_for_training_context'])
        self.assertFalse(result['quality_gate_passed'])

    def test_dropped_keep_remains_in_reconfirmation_denominator(self):
        decision = self.parse({**self.label, 'status': 'reject', 'reason': 'privacy'})[0]
        result = summarize([self.row], self.previous, [decision], [])
        estimate = result['previous_decision_strata']['keep']['keep_reconfirmation']
        self.assertEqual((estimate['count'], estimate['denominator']), (0, 1))
        self.assertEqual(result['consensus_keep_count'], 0)

    def test_expanded_source_excludes_target_and_future_and_validates_selected_lineage(self):
        with tempfile.TemporaryDirectory() as temporary:
            pilot = Path(temporary)
            messages = [asdict(row) for turn in self.fixture.prefix for row in turn.messages]
            messages += [asdict(row) for row in self.fixture.target.messages]
            messages.append(asdict(fixtures.message(4, 'self', '未来消息不可见')))
            (pilot / 'daily-bundles.jsonl').write_text(json.dumps({'messages': messages}) + '\n')
            (pilot / 'policy.json').write_text(json.dumps(self.fixture.config))
            previous = {self.row['sample_id']: {'status': 'uncertain', 'reply_link_correct': False, 'context_complete': False}}
            views, unavailable = build_views(pilot, [self.row], previous, self.policy)
            self.assertEqual(len(views), 2)
            self.assertEqual(unavailable, [])
            for view in views:
                self.assertEqual(view['source_ids'], ['m0', 'm1', 'm2'])
            changed = copy.deepcopy(self.row)
            changed['context_messages'][0]['content'] = '篡改的前文'
            with self.assertRaisesRegex(TopicContractError, 'disagrees'):
                build_views(pilot, [changed], previous, self.policy)

    def test_completed_audit_resumes_without_new_api_calls_or_rewriting_report(self):
        class Response:
            def __enter__(inner):
                return inner
            def __exit__(inner, *args):
                return False
            def read(inner):
                return json.dumps({'output_text': json.dumps({'results': [self.label]}),
                                   'model': 'test-model', 'usage': {'input_tokens': 100, 'output_tokens': 60}}).encode()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pilot, review = root / 'pilot', root / 'review'
            pilot.mkdir(); review.mkdir()
            (pilot / 'manifest.json').write_text('{}')
            (pilot / 'source-audit.json').write_text(json.dumps({'consent_file_sha256': 'test-consent'}))
            (review / 'decisions.json').write_text('{}')
            consent = root / 'consent.json'; consent.write_text('{}')
            auth = root / 'auth.json'; auth.write_text(json.dumps({'OPENAI_API_KEY': 'synthetic-fixture'}))
            kwargs = dict(pilot=pilot, review=review, config_path=ROOT / 'configs/topic-evidence-audit-v1.yaml',
                          consent=consent, output_root=root / 'output', controlled_root=root,
                          base_url='https://example.invalid/', auth_file=auth,
                          authorization_reference='synthetic-test', execute=True)
            with patch('wechat_persona.topic_evidence_audit.verified_pilot', return_value=({}, [self.row])), \
                 patch('wechat_persona.topic_evidence_audit.verified_review', return_value=({}, self.previous)), \
                 patch('wechat_persona.topic_evidence_audit.verify_consent', return_value={'consent_file_sha256': 'test-consent'}), \
                 patch('wechat_persona.topic_evidence_audit.build_views', return_value=([self.view], [])), \
                 patch('wechat_persona.topic_evidence_audit.urlopen', return_value=Response()) as network:
                first = run_audit(**kwargs)
                second = run_audit(**kwargs)
                self.assertEqual(first, second)
                self.assertEqual(network.call_count, 1)
                self.assertEqual(first['usage_cumulative']['requests'], 1)
                path = Path(first['workspace']) / 'consensus.train.draft.jsonl'
                self.assertEqual(json.loads(path.read_text()), self.row)
                self.assertFalse(first['training_ready'])


if __name__ == '__main__':
    unittest.main()
