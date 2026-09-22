from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona._common import digest
from wechat_persona.reply_links import validate_references
from wechat_persona.topic_candidates import build_candidate, freeze_target_attempts, load_policy, load_train_days
from wechat_persona.topic_context import (AtomicMessage, EXCHANGE_POLICY_VERSION, OPENER_POLICY_VERSION,
    TopicContractError, Turn, exchange_dependencies, forward_topics, merge_turns, select_context)


class CharacterTokenizer:
    """Synthetic template with visible role boundaries and deterministic length."""
    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=False):
        text = ''.join(f'<{row["role"]}>{row["content"]}</end>' for row in messages)
        return text + ('<assistant>' if add_generation_prompt else '')

    def __call__(self, text, truncation=False):
        return {'input_ids': [ord(char) for char in text], 'attention_mask': [1] * len(text)}


def message(index, role, text, **kwargs):
    return AtomicMessage(f'm{index}', 's1', 'owner-a', 'train',
                         f'2024-05-20T09:{index:02d}:00+00:00', index,
                         '2024-05-20', role, text, **kwargs)


class TopicCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tokenizer = CharacterTokenizer()
        self.config = load_policy(ROOT / 'configs/daily-topic-sft-v1.yaml')
        self.prefix = [Turn((message(0, 'self', '今天开会有点紧张。'),)),
                       Turn((message(1, 'target', '是要你做汇报吗？'),)),
                       Turn((message(2, 'self', '对，第一次讲这个项目。'),))]
        self.target = Turn((message(3, 'target', '先把开头练两遍。'),))

    def candidate(self, prefix=None, target=None):
        return build_candidate(prefix or self.prefix, target or self.target, self.tokenizer, self.config)

    def test_target_content_does_not_change_selected_context(self):
        first = self.candidate()
        second = self.candidate(target=Turn((replace(self.target.messages[0], content='今晚想吃面。'),)))
        self.assertEqual(first['messages'][:-1], second['messages'][:-1])
        self.assertEqual(first['topic'], second['topic'])
        self.assertNotEqual(first['candidate_sha256'], second['candidate_sha256'])

    def test_future_messages_do_not_revise_prefix_topic_states(self):
        initial = forward_topics(self.prefix)
        later = self.prefix + [self.target, Turn((message(4, 'self', '汇报结束以后才知道的结果。'),))]
        self.assertEqual(initial, forward_topics(later)[:len(initial)])
        later[-1] = Turn((message(4, 'self', '未来改成去医院。'),))
        self.assertEqual(initial, forward_topics(later)[:len(initial)])

    def test_target_reference_cannot_add_context_after_selection(self):
        target = Turn((replace(self.target.messages[0], reply_to='outside-prefix'),))
        with self.assertRaisesRegex(TopicContractError, 'target_reference'):
            self.candidate(target=target)

    def test_delayed_reply_unrecoverable_from_prefix_is_quarantined(self):
        target=Turn((replace(self.target.messages[0],reply_to='m0'),))
        with self.assertRaisesRegex(TopicContractError,'not_reconstructible_online'):
            self.candidate(target=target)

    def test_prefix_reference_keeps_earlier_question_for_delayed_reply(self):
        prefix=[*self.prefix[:-1],Turn((replace(self.prefix[-1].messages[0],reply_to='m0'),))]
        target=Turn((replace(self.target.messages[0],reply_to='m0'),))
        row=self.candidate(prefix=prefix,target=target)
        self.assertIn('m0',row['context_message_ids'])
        self.assertEqual(row['reply_link']['responds_to_ids'],['m0'])

    def test_references_require_same_split_session_and_past(self):
        original = message(0, 'self', '今天开会。')
        for universe, reason in (({'missing':('s2','test')}, 'cross_split'),
                                 ({'missing':('s2','train')}, 'cross_session'),
                                 ({}, 'missing_reference'),
                                 ({'missing':('s1','train')}, 'nonpast')):
            with self.subTest(reason=reason), self.assertRaisesRegex(TopicContractError, reason):
                validate_references([replace(original,reply_to='missing')], universe)

    def test_same_timestamp_requires_stable_order(self):
        first = message(0, 'self', '工作项目。')
        second = replace(message(1, 'target', '好的。'), timestamp=first.timestamp)
        self.assertEqual(len(merge_turns([first,second])), 2)
        with self.assertRaisesRegex(TopicContractError,'order'):
            merge_turns([first,replace(second,order=first.order)])

    def test_topic_switch_and_midnight_prevent_merging(self):
        first = message(0, 'self', '工作项目开会。')
        food = message(1, 'self', '晚饭吃什么？')
        self.assertEqual(len(merge_turns([first,food])),2)
        midnight = replace(food,day='2024-05-21')
        self.assertEqual(len(merge_turns([first,midnight])),2)

    def test_unrelated_same_category_does_not_imply_same_thread(self):
        states = forward_topics([Turn((message(0,'self','公司老板。'),)),
                                 Turn((message(1,'self','晚饭吃什么？'),)),
                                 Turn((message(2,'self','汇报加班。'),))])
        self.assertEqual(states[0]['topic_category'],states[2]['topic_category'])
        self.assertNotEqual(states[0]['topic_id'],states[2]['topic_id'])

    def test_complete_required_turn_is_rejected_when_too_long(self):
        with self.assertRaisesRegex(TopicContractError,'required_context_over_budget'):
            self.candidate(prefix=[Turn((message(0,'self','问题'*2000),))])

    def test_fixed_target_reserve_rejects_oversized_label(self):
        with self.assertRaisesRegex(TopicContractError,'target_or_sequence_over_budget'):
            self.candidate(target=Turn((replace(self.target.messages[0],content='回答'*300),)))

    def test_scope_owner_and_target_overlap_rejected(self):
        with self.assertRaisesRegex(TopicContractError,'context_scope'):
            self.candidate(target=Turn((replace(self.target.messages[0],owner_scope='owner-b'),)))
        with self.assertRaisesRegex(TopicContractError,'target_in_context'):
            self.candidate(target=Turn((replace(self.target.messages[0],message_id='m0'),)))

    def test_semantic_digest_binds_roles_sources_and_order(self):
        row = self.candidate()
        self.assertEqual(row['candidate_sha256'],digest({k:v for k,v in row.items() if k!='candidate_sha256'}))
        mutated = copy.deepcopy(row)
        mutated['context_messages'][0]['order'] += 1
        self.assertNotEqual(row['candidate_sha256'],digest({k:v for k,v in mutated.items() if k!='candidate_sha256'}))

    def test_template_mismatch_rejected(self):
        class BrokenTokenizer(CharacterTokenizer):
            def apply_chat_template(self,messages,tokenize=False,add_generation_prompt=False):
                return super().apply_chat_template(messages,tokenize,add_generation_prompt) + ('broken' if add_generation_prompt else '')
        with self.assertRaisesRegex(TopicContractError,'template_prompt_not_prefix'):
            build_candidate(self.prefix,self.target,BrokenTokenizer(),self.config)

    def test_short_reply_is_not_removed_by_length(self):
        row=self.candidate(target=Turn((replace(self.target.messages[0],content='好'),)))
        self.assertEqual(row['messages'][-1]['content'],'好')

    def test_exchange_policy_restores_earlier_question_without_answer_access(self):
        self.config = load_policy(ROOT / 'configs/daily-topic-sft-v2.yaml')
        row = self.candidate()
        self.assertEqual(row['context_message_ids'], ['m0', 'm1', 'm2'])
        changed = self.candidate(target=Turn((replace(self.target.messages[0], content='换个完全无关的回答'),)))
        self.assertEqual(row['messages'][:-1], changed['messages'][:-1])
        self.assertEqual(row['topic'], changed['topic'])
        self.assertEqual(row['topic']['selector_policy_version'], EXCHANGE_POLICY_VERSION)

    def test_reference_closure_is_transitive_and_keeps_historical_question(self):
        prefix = [Turn((message(0, 'self', '问题甲'),)),
                  Turn((message(1, 'target', '回应甲'),)),
                  Turn((message(2, 'self', '再说一下', reply_to='m1'),)),
                  Turn((message(3, 'target', '继续解释', reply_to='m2'),)),
                  Turn((message(4, 'self', '它具体怎么用', reply_to='m3'),))]
        self.assertEqual(exchange_dependencies(prefix, {4}), set(range(5)))
        with self.assertRaisesRegex(TopicContractError, 'required_context_over_budget'):
            select_context(prefix, self.tokenizer, system='系统', max_length=1024,
                           target_reserve=256, max_turns=4, policy_version=EXCHANGE_POLICY_VERSION)

    def test_v2_required_missing_or_future_reference_fails_closed(self):
        for reference in ('missing', 'm2'):
            prefix = [*self.prefix[:-1], Turn((replace(self.prefix[-1].messages[0], reply_to=reference),))]
            with self.assertRaisesRegex(TopicContractError, 'prefix_reference'):
                select_context(prefix, self.tokenizer, system='系统', max_length=1024,
                               target_reserve=256, policy_version=EXCHANGE_POLICY_VERSION)

    def test_v2_never_keeps_a_reply_without_its_overbudget_question(self):
        prefix = [Turn((message(0, 'self', '问题'*2000),)),
                  Turn((message(1, 'target', '好'),)),
                  Turn((message(2, 'self', '新的问题'),))]
        chosen, _ = select_context(prefix, self.tokenizer, system='系统', max_length=200,
                                  target_reserve=50, policy_version=EXCHANGE_POLICY_VERSION)
        self.assertEqual([turn.ids for turn in chosen], [['m2']])

    def test_v2_recency_keeps_intervening_topic_and_returns_within_turn_budget(self):
        prefix = [Turn((message(i, 'self' if i % 2 == 0 else 'target',
                                '工作项目' if i in (0, 1, 8) else '晚饭吃什么'),)) for i in range(9)]
        chosen, _ = select_context(prefix, self.tokenizer, system='系统', max_length=1024,
                                  target_reserve=256, max_turns=5, policy_version=EXCHANGE_POLICY_VERSION)
        self.assertEqual([turn.ids for turn in chosen], [['m4'], ['m5'], ['m6'], ['m7'], ['m8']])

    def test_frozen_population_preserves_order_and_never_backfills_missing_target(self):
        row = self.candidate()
        attempts = [('a', self.prefix, self.target),
                    ('b', self.prefix, Turn((message(4, 'target', '其他回复'),)))]
        self.assertEqual(freeze_target_attempts(attempts, [row]), attempts[:1])
        with self.assertRaisesRegex(TopicContractError, 'frozen target'):
            freeze_target_attempts(attempts[1:], [row])
        mutated = copy.deepcopy(row)
        mutated['target_messages'][0]['content'] = '改变的回复'
        with self.assertRaisesRegex(TopicContractError, 'frozen target'):
            freeze_target_attempts(attempts, [mutated])

    def test_v3_preserves_target_initiated_history_only_at_known_session_day_start(self):
        prefix = [Turn((message(0, 'target', '明天汇报前我想再练一遍开头。'),)),
                  Turn((message(1, 'self', '几点开始？'),))]
        config = load_policy(ROOT / 'configs/daily-topic-sft-v3.yaml')
        target = Turn((message(2, 'target', '九点开始。'),))
        complete = build_candidate(prefix, target, self.tokenizer, config, prefix_complete_start=True)
        clipped = build_candidate(prefix, target, self.tokenizer, config, prefix_complete_start=False)
        self.assertEqual(complete['context_message_ids'], ['m0', 'm1'])
        self.assertEqual(clipped['context_message_ids'], ['m1'])
        changed = build_candidate(prefix, Turn((replace(target.messages[0], content='无关的其他回复'),)),
                                  self.tokenizer, config, prefix_complete_start=True)
        self.assertEqual(complete['topic'], changed['topic'])
        self.assertEqual(complete['messages'][:-1], changed['messages'][:-1])

    def test_v3_opening_target_reference_requires_complete_start_evidence(self):
        prefix = [Turn((message(0, 'target', '今天计划先整理项目资料。'),)),
                  Turn((message(1, 'self', '这个计划呢？', reply_to='m0'),))]
        with self.assertRaisesRegex(TopicContractError, 'historical_reply_missing'):
            select_context(prefix, self.tokenizer, system='系统', max_length=1024,
                           target_reserve=256, policy_version=OPENER_POLICY_VERSION)
        chosen, _ = select_context(prefix, self.tokenizer, system='系统', max_length=1024,
                                    target_reserve=256, policy_version=OPENER_POLICY_VERSION,
                                    prefix_complete_start=True)
        self.assertEqual([turn.ids for turn in chosen], [['m0'], ['m1']])

    def test_v2_keeps_its_original_orphan_history_rule(self):
        prefix = [Turn((message(0, 'target', '这是一条历史开场消息。'),)),
                  Turn((message(1, 'self', '好的。'),))]
        chosen, _ = select_context(prefix, self.tokenizer, system='系统', max_length=1024,
                                    target_reserve=256, policy_version=EXCHANGE_POLICY_VERSION,
                                    prefix_complete_start=True)
        self.assertEqual([turn.ids for turn in chosen], [['m1']])

    def test_train_selection_never_materializes_test_message_body(self):
        with tempfile.TemporaryDirectory() as temporary:
            source=Path(temporary)
            (source/'redacted').mkdir()
            records=[{
                'message_id':'train-message','timestamp':'2024-05-20T23:59:00+08:00',
                'source_record_index':0,'speaker_role':'self','text_redacted':'今天开会。',
                'message_kind':'text','reply_to':None,
            },{
                'message_id':'test-message','timestamp':'2024-05-21T00:01:00+08:00',
                'source_record_index':1,'speaker_role':'target','text_redacted':'TEST_BODY_SENTINEL',
                'message_kind':'text','reply_to':None,
            }]
            (source/'redacted/messages.jsonl').write_text(
                ''.join(json.dumps(row)+'\n' for row in records),encoding='utf-8')
            decoder=json.loads
            def guarded(value,*args,**kwargs):
                self.assertNotIn('TEST_BODY_SENTINEL',value)
                return decoder(value,*args,**kwargs)
            with patch('wechat_persona.topic_candidates.json.loads',side_effect=guarded):
                grouped,days,coverage=load_train_days(source,{
                    'train-message':('train-session','train'),
                    'test-message':('test-session','test'),
                },'owner-a',self.config)
            self.assertEqual(days,['2024-05-20'])
            self.assertEqual(coverage['selected_train_messages'],1)
            self.assertEqual(list(grouped),[('train-session','2024-05-20')])

    @patch.dict(sys.modules, {'datasets': types.SimpleNamespace(
        Dataset=types.SimpleNamespace(from_list=lambda values: values)
    )})
    def test_driver_encoding_rejects_overlength_and_masks_only_target(self):
        from wechat_persona.training import _tokenize_rows
        row = self.candidate()
        encoded = _tokenize_rows(self.tokenizer, [row], 1024)[0]
        start = row['tokens']['target_start']
        self.assertTrue(all(value == -100 for value in encoded['labels'][:start]))
        self.assertEqual(encoded['labels'][start:], encoded['input_ids'][start:])
        with self.assertRaisesRegex(ValueError, 'exceeds maximum'):
            _tokenize_rows(self.tokenizer, [row], row['tokens']['serialized_tokens'] - 1)
        changed = copy.deepcopy(row)
        changed['messages'][1]['content'] += 'modified'
        with self.assertRaisesRegex(ValueError, 'semantic digest'):
            _tokenize_rows(self.tokenizer, [changed], 1024)


if __name__ == '__main__':
    unittest.main()
