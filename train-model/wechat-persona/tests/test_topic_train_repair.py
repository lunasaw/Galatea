from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from wechat_persona._common import digest, file_digest
from wechat_persona.topic_atomic_evidence import bind_atomic_evidence
from wechat_persona.topic_candidates import build_candidate, load_policy, read_json, write_json
from wechat_persona.topic_context import TopicContractError, Turn
from wechat_persona.topic_context_contiguous import build_contiguous_candidate, select_contiguous
from wechat_persona.topic_quote_provenance import audit_quotes, export_metadata, metadata_object
from wechat_persona.topic_repair_contract import validate_repaired_rows
from wechat_persona.topic_repair_review_queue import partition_review_queue
from wechat_persona.topic_review_protocol_v4 import validate_axes
from wechat_persona.topic_train_repair import load_config, publish_packet
from test_topic_candidates import CharacterTokenizer, message


class MetadataOnlyQuoteTests(unittest.TestCase):
    def test_nested_arrays_escaped_strings_and_final_test_bodies_never_decoded(self):
        body = 'PRIVATE_TEST_BODY: [ { "messages": [1] } ] \\ " 中文'
        payload = {'account': {'messages': [{'content': body}]}, 'messages': [
            {'id': 'local0', 'serverId': 'server0', 'renderType': 'text', 'content': body,
             'other': {'content': body, 'list': [False, None, {'x': body}]}},
            {'id': 'local1', 'serverId': 'server1', 'renderType': 'quote',
             'quoteServerId': 'server0', 'quoteContent': body, 'content': body}], 'trailer': body}
        decoder = json.loads

        def guarded(value, *args, **kwargs):
            self.assertNotIn('PRIVATE_TEST_BODY', value.decode() if isinstance(value, bytes) else value)
            return decoder(value, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'source.json'
            write_json(path, payload)
            with patch('wechat_persona.topic_quote_provenance.json.loads', side_effect=guarded):
                found = list(export_metadata(path))
                normalized = metadata_object(json.dumps({'message_id': 'local0', 'source_record_index': 0,
                                                        'text_redacted': body}).encode())
        self.assertEqual(found, [(0, {'id': 'local0', 'serverId': 'server0', 'renderType': 'text'}),
                                 (1, {'id': 'local1', 'serverId': 'server1', 'renderType': 'quote',
                                      'quoteServerId': 'server0'})])
        self.assertEqual(normalized, {'message_id': 'local0', 'source_record_index': 0})

    def test_escaped_field_names_remain_filtered(self):
        self.assertEqual(metadata_object(b'{"conte\\u006et":"secret","\\u0069d":"safe"}'), {'id': 'safe'})

    def test_malformed_metadata_fails_without_emitting_contents(self):
        for value in (b'{"id":"a","id":"b"}', b'{"id":{"body":"secret"}}',
                      b'{"content":[}', b'{"id" "secret"}', b'{"id":', b'{"id":"a",}'):
            with self.subTest(value=value), self.assertRaises(TopicContractError) as context:
                metadata_object(value)
            self.assertNotIn('secret', str(context.exception))

    def test_quote_namespace_and_scope_are_audited_without_importing_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'manifests').mkdir()
            (root / 'redacted').mkdir()
            consent = root / 'consent.json'
            write_json(consent, {'scope': {'message_types': ['text'], 'media_types': []}})
            raw = root / 'raw.json'
            write_json(raw, {'messages': [
                {'id': 'local0', 'serverId': 'server0', 'renderType': 'text', 'content': 'TRAIN_BODY'},
                {'id': 'local1', 'serverId': 'server1', 'renderType': 'quote', 'quoteServerId': 'server0',
                 'content': 'EXCLUDED_BODY'},
                {'id': 'local2', 'serverId': 'server2', 'renderType': 'text', 'content': 'FINAL_TEST_BODY'}]})
            write_json(root / 'manifests/source_manifest.json', {'source_sha256': file_digest(raw),
                'consent_file_sha256': file_digest(consent), 'message_count': 3})
            write_json(root / 'manifests/split_manifest.json', {'session_ids_by_split': {'train': ['train-session']}})
            write_json(root / 'manifests/lineage.jsonl', {'stage': 'session', 'object_id': 'train-session',
                                                        'source_message_ids': ['local0']})
            write_json(root / 'redacted/messages.jsonl', {'message_id': 'local0', 'source_record_index': 0,
                                                         'message_kind': 'text', 'reply_to': None,
                                                         'text_redacted': 'TRAIN_BODY'})
            decoder = json.loads

            def guarded(value, *args, **kwargs):
                self.assertNotIn('_BODY', value.decode() if isinstance(value, bytes) else value)
                return decoder(value, *args, **kwargs)

            with patch('wechat_persona.topic_quote_provenance.json.loads', side_effect=guarded):
                report = audit_quotes(raw=raw, source=root, consent=consent)
            self.assertEqual(report['quote_reference_matches_id'], 0)
            self.assertEqual(report['quote_reference_matches_server_id'], 1)
            self.assertEqual(report['quote_rows_outside_message_type_scope'], 1)
            self.assertEqual(report['retained_train_messages'], 1)
            self.assertEqual(report['retained_train_normalized_reply_to'], 0)
            self.assertFalse(report['quote_content_imported'])


class ContiguousContextTests(unittest.TestCase):
    def setUp(self):
        self.tokenizer = CharacterTokenizer()
        self.prefix = [Turn((message(0, 'self', '较早的小问题'),)), Turn((message(1, 'target', '好呀'),)),
                       Turn((message(2, 'self', '长问题' * 200),)), Turn((message(3, 'target', '那就这样'),)),
                       Turn((message(4, 'self', '晚上一起散步吗'),))]
        self.kwargs = {'system': '系统', 'max_length': 250, 'target_reserve': 80, 'max_turns': 16,
                       'prefix_complete_start': True}

    def test_unfit_exchange_stops_suffix_instead_of_stitching_older_turns(self):
        chosen, info = select_contiguous(self.prefix, self.tokenizer, **self.kwargs)
        self.assertEqual([t.ids for t in chosen], [['m4']])
        self.assertEqual(info['stop_reason'], 'older_exchange_over_budget')
        self.assertEqual(info['internal_omitted_turns'], 0)

    def test_explicit_reference_requires_entire_intervening_suffix(self):
        prefix = self.prefix[:-1] + [Turn((replace(self.prefix[-1].messages[0], reply_to='m0'),))]
        with self.assertRaisesRegex(TopicContractError, 'required_contiguous_context_over_budget'):
            select_contiguous(prefix, self.tokenizer, **self.kwargs)

    def test_transitive_references_and_historical_replies_remain_whole(self):
        prefix = [Turn((message(0, 'self', '周末去公园吗'),)),
                  Turn((message(1, 'target', '好的，那我准备午饭', reply_to='m0'),)),
                  Turn((message(2, 'self', '那我带饮料', reply_to='m1'),))]
        chosen, _ = select_contiguous(prefix, self.tokenizer, **self.kwargs)
        self.assertEqual(chosen, prefix)

    def test_legitimate_opening_target_only_allowed_at_complete_start(self):
        prefix = [Turn((message(0, 'target', '早上好呀'),)), Turn((message(1, 'self', '早'),))]
        chosen, _ = select_contiguous(prefix, self.tokenizer, **self.kwargs)
        self.assertEqual(chosen, prefix)
        chosen, _ = select_contiguous(prefix, self.tokenizer, **{**self.kwargs, 'prefix_complete_start': False})
        self.assertEqual(chosen, prefix[1:])

    def test_mixed_split_duplicate_id_or_future_order_fail_closed(self):
        for changed in (replace(self.prefix[0].messages[0], split='test'),
                        replace(self.prefix[0].messages[0], order=5),
                        replace(self.prefix[0].messages[0], message_id='m4')):
            with self.subTest(changed=changed), self.assertRaises(TopicContractError):
                select_contiguous([Turn((changed,)), *self.prefix[1:]], self.tokenizer, **self.kwargs)

    def test_rebuilt_context_is_unreviewed_and_target_never_selects_prefix(self):
        config = load_policy(ROOT / 'configs/daily-topic-sft-v3.yaml')
        prefix = [self.prefix[0], self.prefix[1], self.prefix[4]]
        target = Turn((message(5, 'target', '好呀，晚饭后去'),))
        old = build_candidate(prefix, target, self.tokenizer, config, prefix_complete_start=True)
        config['context']['policy_version'] = 'prefix-contiguous-exchanges-v4'
        first = build_contiguous_candidate(prefix, target, self.tokenizer, config, parent=old, prefix_complete_start=True)
        other_target = Turn((replace(target.messages[0], content='明早再去吧'),))
        other_parent = build_candidate(prefix, other_target, self.tokenizer,
                                       load_policy(ROOT / 'configs/daily-topic-sft-v3.yaml'), prefix_complete_start=True)
        second = build_contiguous_candidate(prefix, other_target, self.tokenizer, config,
                                             parent=other_parent, prefix_complete_start=True)
        self.assertEqual(first['messages'][:-1], second['messages'][:-1])
        self.assertEqual(first['topic'], second['topic'])
        self.assertEqual(first['target_messages'], old['target_messages'])
        self.assertEqual(first['reply_link']['responds_to_ids'], [])
        self.assertTrue(first['fresh_review_required'])
        self.assertFalse(first['old_quality_decisions_transferred'])
        self.assertEqual(first['review_status'], 'uncertain')


class AtomicEvidenceRepairTests(unittest.TestCase):
    def setUp(self):
        self.config = load_policy(ROOT / 'configs/daily-topic-sft-v3.yaml')
        self.row = build_candidate([Turn((message(0, 'self', '你今天几点下班'),
                                          message(1, 'self', '吃晚饭了吗')))],
                                   Turn((message(2, 'target', '六点下班'),)), CharacterTokenizer(), self.config)
        axes = {'relation': 'direct_answer', 'context_status': 'sufficient', 'communicative_value': 'useful',
                'risk_status': 'clear', 'risk_flags': [], 'confidence': .95, 'responds_to_indices': [0],
                'required_context_indices': [0], 'uncertainties': []}
        decision = validate_axes(self.row, axes)
        self.pair = [{**decision, 'judge': judge} for judge in ('gpt', 'claude')]
        self.case = {'sample_id': self.row['sample_id'], 'candidate_sha256': self.row['candidate_sha256'],
                     'disposition': 'selected', 'prior_hard_risks': [], 'shared_responds_to_ids': ['m0'],
                     'required_context_ids': ['m0'], 'decision_sha256': {d['judge']: digest(d) for d in self.pair}}

    def test_atomic_anchor_replaces_turn_hypothesis_without_changing_text_or_tokens(self):
        before = deepcopy(self.row)
        result = bind_atomic_evidence(self.row, self.pair, self.case, 'a' * 64)
        self.assertEqual(self.row, before)
        self.assertEqual(result['reply_link']['responds_to_ids'], ['m0'])
        self.assertEqual(result['reply_link_hypothesis']['responds_to_ids'], ['m0', 'm1'])
        for key in ('messages', 'context_messages', 'target_messages', 'tokens', 'cutoff'):
            self.assertEqual(result[key], self.row[key])
        self.assertNotEqual(result['candidate_sha256'], self.row['candidate_sha256'])
        self.assertFalse(result['reply_link']['source_reply_to_claimed'])
        self.assertFalse(result['formal_training_eligible'])
        self.assertEqual(result['review_kind'], 'machine')

    def test_previous_hard_risk_or_not_selected_cannot_enter_draft(self):
        for changes in ({'prior_hard_risks': ['privacy']}, {'disposition': 'axes_keep_not_agreed'},
                        {'shared_responds_to_ids': ['m1']}, {'required_context_ids': []}):
            with self.subTest(changes=changes), self.assertRaises(TopicContractError):
                bind_atomic_evidence(self.row, self.pair, {**self.case, **changes}, 'a' * 64)

    def test_tampered_candidate_decision_or_machine_identity_rejected(self):
        for field, value in (('responds_to_ids', ['m1']), ('review_kind', 'human'), ('context_complete', False)):
            pair = deepcopy(self.pair)
            pair[0][field] = value
            with self.subTest(field=field), self.assertRaises(TopicContractError):
                bind_atomic_evidence(self.row, pair, self.case, 'a' * 64)
        changed = deepcopy(self.row)
        changed['messages'][1]['content'] += '更改输入'
        with self.assertRaises(TopicContractError):
            bind_atomic_evidence(changed, self.pair, self.case, 'a' * 64)

    def test_duplicate_judge_and_wrong_population_rejected(self):
        with self.assertRaises(TopicContractError):
            bind_atomic_evidence(self.row, self.pair[:1] * 2, self.case, 'a' * 64)
        changed = {**self.row, 'split': 'validation'}
        changed['candidate_sha256'] = digest({k: v for k, v in changed.items() if k != 'candidate_sha256'})
        with self.assertRaises(TopicContractError):
            bind_atomic_evidence(changed, self.pair, self.case, 'a' * 64)

    def test_v2_contract_rejects_status_human_or_source_reference_escalation(self):
        result = bind_atomic_evidence(self.row, self.pair, self.case, 'a' * 64)
        validate_repaired_rows([result])
        for field, value in (('human_review_completed', True), ('formal_training_eligible', True),
                             ('schema_version', 'topic-reply-candidate-v1'), ('review_kind', 'human')):
            changed = deepcopy(result)
            changed[field] = value
            changed['candidate_sha256'] = digest({k: v for k, v in changed.items() if k != 'candidate_sha256'})
            with self.subTest(field=field), self.assertRaises(TopicContractError):
                validate_repaired_rows([changed])
        changed = deepcopy(result)
        changed['reply_link']['source_reply_to_claimed'] = True
        changed['candidate_sha256'] = digest({k: v for k, v in changed.items() if k != 'candidate_sha256'})
        with self.assertRaises(TopicContractError):
            validate_repaired_rows([changed])


class ImmutableRepairPacketTests(unittest.TestCase):
    def test_replay_permissions_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'private' / 'packet'
            artifacts = {'draft.jsonl': [{'sample': 'fixture', 'formal_training_eligible': False}],
                         'report.json': {'count': 1, 'external_requests': 0}}
            publish_packet(output, {'source_sha256': 'a' * 64}, artifacts)
            first = (output / 'manifest.json').read_bytes()
            publish_packet(output, {'source_sha256': 'a' * 64}, artifacts)
            self.assertEqual((output / 'manifest.json').read_bytes(), first)
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir()))
            with self.assertRaises(TopicContractError):
                publish_packet(output, {'source_sha256': 'a' * 64}, {**artifacts, 'report.json': {'count': 2}})
            (output / 'report.json').write_text('{}')
            with self.assertRaises(TopicContractError):
                publish_packet(output, {'source_sha256': 'a' * 64}, artifacts)

    def test_frozen_repair_policy_cannot_change_split_or_transfer_labels(self):
        policy = load_config(ROOT / 'configs/topic-train-repair-v1.yaml')
        self.assertEqual(policy['external_requests'], 0)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'config.yaml'
            for overrides in ({'allowed_splits': ['validation']}, {'reuse_verdict_for_changed_context': True}):
                path.write_text(json.dumps({**policy, **overrides}))
                with self.assertRaises(TopicContractError):
                    load_config(path)


class RepairReviewQueueTests(unittest.TestCase):
    def setup_case(self, *, omit_required=False, risk=False):
        helper = AtomicEvidenceRepairTests()
        helper.setUp()
        parent, case, config = helper.row, deepcopy(helper.case), helper.config
        config['context']['policy_version'] = 'prefix-contiguous-exchanges-v4'
        config['context']['max_context_turns'] = 1 if omit_required else 16
        prefix = [Turn((message(0, 'self', '你今天几点下班'),)), Turn((message(1, 'self', '吃晚饭了吗'),))]
        new = build_contiguous_candidate(prefix, Turn((message(2, 'target', '六点下班'),)),
                                         CharacterTokenizer(), config, parent=parent, prefix_complete_start=True)
        if risk:
            case['prior_hard_risks'] = ['privacy_leak']
        new['prior_hard_risks'] = case['prior_hard_risks']
        new['candidate_sha256'] = digest({k: v for k, v in new.items() if k != 'candidate_sha256'})
        comparison = {'parent_sample_id': parent['sample_id'], 'status': 'built',
                      'candidate_sha256': new['candidate_sha256'],
                      'old_review_required_context_missing': ['m0'] if omit_required else []}
        return new, case, comparison

    def test_known_regression_and_prior_hard_risk_excluded_without_replacement(self):
        for kwargs, reason in (({'omit_required': True}, 'known_required_context_regression'),
                               ({'risk': True}, 'prior_hard_risk_requires_adjudication')):
            new, case, comparison = self.setup_case(**kwargs)
            queue, dispositions = partition_review_queue([new], [case], [comparison])
            self.assertEqual(queue, [])
            self.assertEqual(len(dispositions), 1)
            self.assertEqual(dispositions[0]['disposition'], reason)

    def test_preserved_context_is_queued_for_review_without_inheriting_keep(self):
        new, case, comparison = self.setup_case()
        queue, dispositions = partition_review_queue([new], [case], [comparison])
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]['review_status'], 'uncertain')
        self.assertFalse(queue[0]['formal_training_eligible'])
        self.assertEqual(dispositions[0]['disposition'], 'ready_for_fresh_machine_review')

    def test_lost_context_cannot_be_hidden_by_editing_comparison(self):
        new, case, comparison = self.setup_case(omit_required=True)
        comparison['old_review_required_context_missing'] = []
        with self.assertRaises(TopicContractError):
            partition_review_queue([new], [case], [comparison])
        with self.assertRaises(TopicContractError):
            partition_review_queue([new, new], [case], [comparison])


if __name__ == '__main__':
    unittest.main()
