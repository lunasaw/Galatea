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

from wechat_persona._common import digest, file_digest
from wechat_persona.topic_adjudication import (
    build_adjudication, full_prefixes, load_fixtures, load_policy,
    primary_queue, render_page, triage, verify_manifest,
)
from wechat_persona.topic_candidates import load_policy as load_candidate_policy
from wechat_persona.topic_context import TopicContractError
from wechat_persona.topic_evidence_audit import evidence_view, parse_response
import test_topic_candidates as fixtures


class AdjudicationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TopicCandidateTests()
        self.fixture.setUp()
        self.fixture.config = load_candidate_policy(ROOT / 'configs/daily-topic-sft-v3.yaml')
        self.row = self.fixture.candidate()
        self.policy_path = ROOT / 'configs/topic-adjudication-v1.yaml'
        self.policy = load_policy(self.policy_path)
        label = {'index': 0, 'status': 'keep', 'reason': 'usable_reply', 'confidence': .95,
                 'reply_link_correct': True, 'context_complete': True,
                 'responds_to_indices': [2], 'required_context_indices': [0, 1, 2]}
        decision = parse_response({'output_text': json.dumps({'results': [label]})},
                                 [evidence_view(self.row, self.row['context_messages'], 'selected')], .9)[0]
        self.case = {'sample_id': self.row['sample_id'], 'disposition': 'selected', 'prior_hard_risks': [],
                     'reference_decision': decision, 'independent_decision': copy.deepcopy(decision)}
        self.prefix = {'messages': self.row['context_messages'], 'source_ids': self.row['context_message_ids'],
                       'unavailable': None, 'turn_by_message': {'m0': 0, 'm1': 1, 'm2': 2}}

    def test_policy_cannot_change_labels_or_governance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.yaml'
            for field in ('human_review_completed', 'existing_labels_changed', 'selection_policy_changed'):
                policy = copy.deepcopy(self.policy)
                policy['governance'][field] = True
                path.write_text(json.dumps(policy))
                with self.assertRaises(TopicContractError):
                    load_policy(path)

    def test_synthetic_references_cover_short_replies_and_boundaries(self):
        _, cases = load_fixtures(self.policy_path, self.policy)
        by_id = {row['id']: row for row in cases}
        self.assertEqual(len(cases), 18)
        for key in ('short_accept', 'greeting', 'thanks', 'short_care'):
            self.assertEqual(by_id[key]['expected']['status'], 'keep')
        self.assertFalse(by_id['unresolved_choice']['expected']['context_complete'])
        self.assertFalse(by_id['target_self_continuation']['expected']['reply_link_correct'])
        self.assertEqual(by_id['one_atomic_question']['expected']['responds_to_indices'], [0])
        self.assertEqual(by_id['two_atomic_questions']['expected']['responds_to_indices'], [0, 1])
        self.assertEqual(by_id['third_party_sensitive']['expected']['status'], 'reject')

    def test_risk_and_anchor_queues_have_priority_over_other_disagreement(self):
        self.case['independent_decision']['reply_link_correct'] = False
        for queue in ('prior_hard_risk_requires_adjudication', 'reply_anchor_disagreement'):
            self.case['disposition'] = queue
            self.assertEqual(primary_queue(self.case), queue)

    def test_quality_disagreement_axes_are_separate(self):
        self.case['disposition'] = 'cross_family_keep_not_agreed'
        independent = self.case['independent_decision']
        independent.update(status='uncertain', reply_link_correct=False, context_complete=False)
        self.assertEqual(primary_queue(self.case), 'reply_link_disagreement')
        independent['reply_link_correct'] = True
        self.assertEqual(primary_queue(self.case), 'context_sufficiency_disagreement')
        independent['context_complete'] = True
        self.assertEqual(primary_queue(self.case), 'eligibility_disagreement')
        self.case['reference_decision']['status'] = 'uncertain'
        self.assertEqual(primary_queue(self.case), 'both_nonkeep')

    def test_short_reply_does_not_change_selection(self):
        short = copy.deepcopy(self.row)
        short['messages'][-1]['content'] = '好'
        result = triage(short, self.case, self.prefix, self.policy)
        self.assertTrue(result['short_reply'])
        self.assertEqual(result['queue'], 'selected')
        self.assertFalse(result['reply_length_is_rejection_rule'])
        self.assertFalse(result['human_review_completed'])

    def test_same_turn_disjoint_anchors_stay_pending(self):
        self.case['disposition'] = 'reply_anchor_disagreement'
        self.case['reference_decision']['responds_to_ids'] = ['m0']
        self.prefix['turn_by_message']['m2'] = 0
        result = triage(self.row, self.case, self.prefix, self.policy)
        self.assertEqual(result['anchor_relation'], 'same_source_turn_distinct_anchors')
        self.assertEqual(result['adjudication_status'], 'pending')
        self.assertFalse(result['same_turn_is_approval_rule'])

    def test_diagnostic_missing_ids_are_hints_not_new_training_context(self):
        self.prefix['source_ids'] = ['older', *self.prefix['source_ids']]
        diagnostic = {**self.case['reference_decision'], 'required_context_ids': ['older', 'm2']}
        original = copy.deepcopy(self.row)
        result = triage(self.row, self.case, self.prefix, self.policy, diagnostic)
        self.assertEqual(result['omitted_message_count'], 1)
        self.assertEqual(result['prior_expanded_diagnostic']['required_ids_missing_from_current_input'], ['older'])
        self.assertFalse(result['prior_expanded_diagnostic']['is_proof_of_true_omission'])
        self.assertFalse(result['diagnostic_evidence_used_for_context_selection'])
        self.assertEqual(self.row, original)

    def write_pilot(self, path, *, duplicate=False, split='train'):
        messages = [asdict(m) for t in self.fixture.prefix for m in t.messages]
        messages += [asdict(m) for m in self.fixture.target.messages]
        messages += [asdict(fixtures.message(4, 'self', '这条未来消息不能进入诊断视图。'))]
        bundle = {'split': split, 'messages': messages}
        (path / 'policy.json').write_text(json.dumps(self.fixture.config))
        (path / 'daily-bundles.jsonl').write_text((json.dumps(bundle) + '\n') * (2 if duplicate else 1))

    def test_full_prefix_excludes_target_future_and_keeps_original_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            pilot = Path(directory)
            self.write_pilot(pilot)
            row = copy.deepcopy(self.row)
            row['context_messages'] = [m for m in row['context_messages'] if m['role'] == 'self']
            row['context_message_ids'] = [m['message_id'] for m in row['context_messages']]
            prefix = full_prefixes(pilot, [row])[row['sample_id']]
            self.assertEqual(prefix['source_ids'], ['m0', 'm1', 'm2'])
            self.assertNotEqual(prefix['turn_by_message']['m0'], prefix['turn_by_message']['m2'])
            self.assertNotIn('m3', prefix['source_ids'])
            self.assertNotIn('m4', prefix['source_ids'])

    def test_prefix_rejects_duplicate_target_changed_source_and_test_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            pilot = Path(directory)
            self.write_pilot(pilot, duplicate=True)
            with self.assertRaisesRegex(TopicContractError, 'disagrees'):
                full_prefixes(pilot, [self.row])
            self.write_pilot(pilot)
            changed = copy.deepcopy(self.row)
            changed['context_messages'][0]['content'] = 'changed'
            with self.assertRaisesRegex(TopicContractError, 'disagrees'):
                full_prefixes(pilot, [changed])
            self.write_pilot(pilot, split='test')
            with self.assertRaisesRegex(TopicContractError, 'train-only'):
                full_prefixes(pilot, [self.row])

    def test_render_escapes_chat_and_shows_both_anchors_without_writes(self):
        row = copy.deepcopy(self.row)
        row['messages'][-1]['content'] = '<script>alert(1)</script>'
        result = triage(row, self.case, self.prefix, self.policy)
        page = render_page([row], [self.case], [result], {row['sample_id']: self.prefix}, self.policy)
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', page)
        self.assertIn('GPT 所指的回应对象', page)
        self.assertIn('Claude 所指的回应对象', page)
        self.assertNotIn('fetch(', page)
        self.assertIn('本页只读', page)

    def test_manifest_rejects_missing_files_tampering_and_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p / 'report.json').write_text('{}')
            for outputs in ({}, {'report.json': 'wrong'}, {'../elsewhere': 'wrong'}):
                manifest = {'output_digests': outputs}
                manifest['manifest_sha256'] = digest(manifest)
                (p / 'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaises(TopicContractError):
                    verify_manifest(p, {'report.json'})

    def test_offline_build_plan_replay_and_no_eligibility_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {key: root / key for key in ('before', 'after', 'review', 'audit', 'cross')}
            for path in paths.values():
                path.mkdir()
                (path / 'manifest.json').write_text('{}')
                (path / 'decisions.json').write_text('{}')
                (path / 'source-audit.json').write_text(json.dumps({'consent_file_sha256': 'fixture-consent'}))
            self.write_pilot(paths['after'])
            consent = root / 'consent.json'
            consent.write_text('{}')
            kwargs = dict(**paths, consent=consent, policy_path=self.policy_path,
                          cross_policy_path=ROOT / 'configs/topic-cross-review-v1.yaml',
                          output_root=root / 'output', controlled_root=root)
            input_hashes = {p: file_digest(p / 'manifest.json') for p in paths.values()}
            with patch('wechat_persona.topic_adjudication.verify_consent', return_value={'consent_file_sha256': 'fixture-consent'}), \
                 patch('wechat_persona.topic_adjudication.verified_cross', return_value=([self.row], [self.row], [self.case], {'roster': []})), \
                 patch('wechat_persona.topic_adjudication.diagnostic_bindings', return_value={}), \
                 patch('urllib.request.urlopen', side_effect=AssertionError('offline only')):
                plan = build_adjudication(**kwargs)
                self.assertFalse(Path(plan['output_dir']).exists())
                result = build_adjudication(**kwargs, execute=True)
                output = Path(result['output_dir'])
                before = {p.name: file_digest(p) for p in output.iterdir()}
                repeated = build_adjudication(**kwargs, execute=True)
                self.assertEqual(repeated['status'], 'already_built')
                self.assertEqual(result['report'], repeated['report'])
                self.assertEqual(before, {p.name: file_digest(p) for p in output.iterdir()})
                self.assertFalse(result['report']['formal_training_eligible'])
                self.assertEqual(result['report']['new_adjudications'], 0)
                for filename in ('report.json', 'triage.jsonl'):
                    self.assertNotIn(self.row['messages'][-1]['content'], (output / filename).read_text())
                self.assertEqual(output.stat().st_mode & 0o777, 0o700)
                self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir()))
                self.assertEqual(input_hashes, {p: file_digest(p / 'manifest.json') for p in paths.values()})
                (output / 'triage.jsonl').write_text('{}\n')
                with self.assertRaisesRegex(TopicContractError, 'artifact changed'):
                    build_adjudication(**kwargs, execute=True)


if __name__ == '__main__':
    unittest.main()
