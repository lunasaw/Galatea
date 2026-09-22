from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona import topic_validation_duplicates as audit
from wechat_persona._common import digest, file_digest
from wechat_persona.topic_candidates import write_json, write_jsonl
from wechat_persona.topic_context import TopicContractError
from test_topic_validation_review import validation_fixture


class ValidationDuplicateTests(unittest.TestCase):
    def setUp(self):
        self.policy = audit.load_policy(ROOT / 'configs/topic-validation-duplicates-v1.yaml')

    def row(self, sid, split, context, reply):
        value = {'sample_id': sid, 'split': split, 'messages': [
            {'role': 'system', 'content': '相同系统提示' * 100},
            {'role': 'user', 'content': context}, {'role': 'assistant', 'content': reply}]}
        return {**value, 'candidate_sha256': digest(value)}

    def test_normalized_exact_pair_detected_without_system_prompt_influence(self):
        train = self.row('a', 'train', 'self: 开会  ABC', '谢谢')
        same = self.row('b', 'validation', 'self: 开会 ＡＢＣ', '谢谢')
        different = self.row('c', 'validation', 'self: 晚饭吃什么', '谢谢')
        pairs, report = audit.compare([train], [same, different], self.policy)
        self.assertEqual(len(pairs), 1)
        self.assertTrue(pairs[0]['normalized_exact_pair'])
        self.assertEqual(report['pairs_checked'], {'cross_split': 2, 'within_validation': 1})
        self.assertFalse(report['full_training_population_audited'])

    def test_long_near_duplicate_needs_both_context_and_reply(self):
        context = 'self: ' + ''.join(chr(0x4e00 + i) for i in range(200))
        reply = ''.join(chr(0x5000 + i) for i in range(200))
        train = self.row('a', 'train', context, reply)
        near = self.row('b', 'validation', context + '。', reply + '。')
        different = self.row('c', 'validation', context, '完全不同的短回复')
        pairs, _ = audit.compare([train], [near, different], self.policy)
        self.assertEqual(len(pairs), 1)
        self.assertGreaterEqual(pairs[0]['context_similarity'], .9)
        self.assertFalse(pairs[0]['normalized_exact_pair'])

    def test_short_variants_do_not_match_and_input_order_is_retained(self):
        train = self.row('a', 'train', 'self: 好吗', '好的')
        variants = [self.row('b', 'validation', 'self: 好吗', '好'),
                    self.row('c', 'validation', 'target: 好吗', '好的')]
        self.assertEqual(audit.compare([train], variants, self.policy)[0], [])
        self.assertNotEqual(audit.normalized('self: 一 target: 二'), audit.normalized('target: 二 self: 一'))

    def test_wrong_split_colliding_ids_and_population_cap_fail_closed(self):
        a = self.row('a', 'train', 'self: 问题', '回复')
        b = self.row('b', 'validation', 'self: 问题', '回复')
        for train, validation in (([a], [{**b, 'split': 'test'}]), ([a], [{**b, 'sample_id': 'a'}]),
                                  ([a] * 201, [b])):
            with self.assertRaises(TopicContractError):
                audit.compare(train, validation, self.policy)

    def test_private_publication_replay_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet, development = root / 'packet', root / 'development'
            packet.mkdir()
            development.mkdir()
            fixture = {'id': 'fixture', 'past': [['self', '几点上班？']], 'reply': '九点。'}
            candidate = validation_fixture(fixture)
            train = self.row('train-a', 'train', candidate['messages'][1]['content'], candidate['messages'][-1]['content'])
            write_json(packet / 'identity.json', {'candidate_digests': [candidate['candidate_sha256']]})
            write_jsonl(packet / 'validation.candidates.jsonl', [candidate])
            manifest = {'identity': {'candidate_digests': [candidate['candidate_sha256']]},
                        'output_digests': {p.name: file_digest(p) for p in packet.iterdir()}}
            manifest['manifest_sha256'] = digest(manifest)
            write_json(packet / 'manifest.json', manifest)
            write_json(development / 'manifest.json', {})
            policy = deepcopy(self.policy)
            policy.update(packet_manifest_sha256=file_digest(packet / 'manifest.json'),
                          development_manifest_sha256=file_digest(development / 'manifest.json'))
            args = dict(packet=packet, development=development, config_path=ROOT / 'configs/topic-validation-duplicates-v1.yaml',
                        output_root=root / 'output', controlled_root=root)
            with patch.object(audit, 'load_policy', return_value=policy), \
                 patch.object(audit, 'verified_pilot', return_value=({}, [train])):
                before = {p: file_digest(p) for d in (packet, development) for p in d.iterdir()}
                plan = audit.run_audit(**args)
                self.assertFalse(args['output_root'].exists())
                result = audit.run_audit(**args, execute=True)
                self.assertEqual(result['workspace'], plan['workspace'])
                self.assertEqual(result['report']['cross_split_flagged_pairs'], 1)
                self.assertFalse(result['report']['selection_changed'])
                self.assertEqual(audit.run_audit(**args, execute=True)['status'], 'already_built')
                self.assertTrue(all(file_digest(p) == sha for p, sha in before.items()))
                output = Path(result['workspace'])
                self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir()))
                (output / 'pairs.jsonl').write_text('')
                with self.assertRaises(TopicContractError):
                    audit.run_audit(**args, execute=True)


if __name__ == '__main__':
    unittest.main()
