from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from test_topic_candidates import CharacterTokenizer, message
from wechat_persona import topic_validation as validation
from wechat_persona._common import digest, file_digest
from wechat_persona.topic_candidates import build_candidate, load_policy, read_json
from wechat_persona.topic_context import TopicContractError, Turn


class ValidationPreparationTests(unittest.TestCase):
    def setUp(self):
        self.policy = validation.load_policy(ROOT / 'configs/topic-validation-v1.yaml')
        self.config = load_policy(ROOT / 'configs/daily-topic-sft-v3.yaml')
        self.tokenizer = CharacterTokenizer()
        self.prefix = [Turn((message(0, 'self', '明天几点上班？'),))]
        self.target = Turn((message(1, 'target', '明天九点。'),))
        self.train = build_candidate(self.prefix, self.target, self.tokenizer, self.config)

    def group(self, sid='validation-session'):
        messages = [replace(m, split='validation', session_id=sid, message_id=sid + m.message_id)
                    for turn in self.prefix + [self.target] for m in turn.messages]
        return {(sid, messages[0].day): messages}, {m.message_id: (sid, 'validation') for m in messages}

    def population(self, grouped=None, universe=None, development=None):
        if grouped is None:
            grouped, universe = self.group()
        return validation.build_population(grouped, universe, self.tokenizer, self.config,
                                           self.policy, development or [])

    def test_metadata_scan_never_decodes_train_test_or_unchosen_validation_bodies(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / 'redacted').mkdir()
            records = []
            universe = {}
            for i, (split, day) in enumerate([('train', '20'), ('test', '20'),
                                              ('validation', '20'), ('validation', '21')]):
                row = {'message_id': str(i), 'timestamp': f'2024-05-{day}T09:00:00+08:00',
                       'source_record_index': i, 'speaker_role': 'self', 'message_kind': 'text',
                       'text_redacted': 'FORBIDDEN_BODY' if i < 2 else '允许读取', 'reply_to': None}
                records.append(row)
                universe[str(i)] = ('session-' + str(i), split)
            self.policy['selection']['max_days'] = 1
            chosen = min(['2024-05-20', '2024-05-21'],
                         key=lambda day: digest({'day': day, 'seed': 53}))
            for row in records[2:]:
                if row['timestamp'][:10] != chosen:
                    row['text_redacted'] = 'FORBIDDEN_BODY'
            records.append({**records[0], 'message_id': 'excluded-from-session', 'text_redacted': 'FORBIDDEN_BODY'})
            (source / 'redacted/messages.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
            original = json.loads
            def guarded(text, *args, **kwargs):
                self.assertNotIn('FORBIDDEN_BODY', text)
                return original(text, *args, **kwargs)
            with patch('wechat_persona.topic_validation.json.loads', side_effect=guarded):
                grouped, days, coverage = validation.load_validation_days(source, universe, 'owner-a',
                                                                          self.policy, 'Asia/Shanghai')
            self.assertEqual(days, [chosen])
            self.assertEqual(coverage['selected_validation_messages'], 1)
            self.assertEqual(coverage['unassigned_source_records_skipped_without_body_decode'], 1)
            self.assertEqual(len(grouped), 1)
            universe['missing'] = ('missing-session', 'validation')
            with self.assertRaisesRegex(TopicContractError, 'coverage'):
                validation.load_validation_days(source, universe, 'owner-a', self.policy, 'Asia/Shanghai')

    def test_validation_schema_is_separate_and_labels_preserve_original_reply(self):
        rows, evidence, _, _ = self.population()
        row = rows[0]
        jsonschema.validate(row, validation.validation_schema())
        self.assertEqual(row['messages'], self.train['messages'])
        self.assertEqual(row['tokens'], self.train['tokens'])
        self.assertEqual(row['schema_version'], validation.CANDIDATE_VERSION)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(row, read_json(ROOT / 'schemas/topic-reply-candidate.schema.json'))
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(self.train, validation.validation_schema())
        self.assertEqual(evidence[0]['reference']['status'], 'available')
        self.assertEqual(row['candidate_sha256'], digest({k: v for k, v in row.items() if k != 'candidate_sha256'}))

    def test_wire_payload_preserves_frozen_v4_prompt_schema_and_content(self):
        rows, _, _, _ = self.population()
        policy = validation.axes.load_config(ROOT / 'configs/topic-axes-review-v4.yaml')
        for judge in policy['judges'].values():
            self.assertEqual(validation.validation_payload(rows[0], judge, self.policy['prospective_review']),
                validation.axes.wire_payload([self.train], judge, self.policy['prospective_review']))
        with self.assertRaises(TopicContractError):
            validation.validation_payload(self.train, policy['judges']['gpt'], self.policy['prospective_review'])

    def test_development_overlap_and_misassigned_lineage_fail_closed(self):
        grouped, universe = self.group(self.train['session_id'])
        with self.assertRaisesRegex(TopicContractError, 'overlap'):
            self.population(grouped, universe, [self.train])
        grouped, universe = self.group()
        universe[next(iter(universe))] = ('other', 'test')
        with self.assertRaisesRegex(TopicContractError, 'overlap'):
            self.population(grouped, universe)

    def test_exact_duplicates_are_quarantined_and_counted(self):
        grouped, universe = self.group()
        other, more = self.group('second-session')
        grouped.update(other)
        universe.update(more)
        rows, _, quarantine, report = self.population(grouped, universe)
        self.assertEqual(len(rows), 1)
        self.assertEqual(report['counts']['quarantined_target_attempts'], 1)
        self.assertEqual(quarantine[0]['reason'], 'exact_duplicate_development_or_validation_window')
        with_development, _, _, report = self.population(grouped, universe, [self.train])
        self.assertEqual(with_development, [])
        self.assertEqual(report['counts']['quarantined_target_attempts'], 2)

    def test_order_independence_budget_and_failed_targets_remain_in_denominator(self):
        grouped, universe = {}, {}
        for i in range(5):
            rows, mapping = self.group('session-' + str(i))
            messages = next(iter(rows.values()))
            messages[-1] = replace(messages[-1], content=('回答' * 400 if i == 0 else f'明天九点，第{i}次。'))
            grouped.update(rows)
            universe.update(mapping)
        forward = self.population(grouped, universe)
        reverse = self.population(dict(reversed(list(grouped.items()))), universe)
        self.assertEqual(forward, reverse)
        counts = forward[-1]['counts']
        self.assertEqual(counts['quarantined_target_attempts'], 1)
        self.assertEqual(counts['eligible_target_attempts'], 5)
        self.assertEqual(counts['candidates'] + counts['quarantined_target_attempts'] + counts['unprocessed_target_attempts'], 5)
        self.policy['selection']['max_target_attempts'] = 2
        bounded = self.population(grouped, universe)[-1]['counts']
        self.assertEqual(bounded['processed_target_attempts'], 2)
        self.assertEqual(bounded['unprocessed_target_attempts'], 3)

    def test_evidence_rejects_test_future_and_target_overlap_and_escapes_page(self):
        rows, evidence, _, _ = self.population()
        row = rows[0]
        for key, value in [('split', 'test'), ('order', 9999), ('message_id', row['target_message_ids'][0])]:
            messages = deepcopy(row['context_messages'])
            messages[0][key] = value
            with self.subTest(key=key), self.assertRaises(TopicContractError):
                validation.validation_view(row, messages, 'expanded_reference')
        row['messages'][1]['content'] = '<script>alert(1)</script>'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'review.html'
            validation.write_blind_page(path, rows, evidence)
            self.assertNotIn('<script>', path.read_text())
            self.assertIn('&lt;script&gt;', path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_plan_execute_replay_tamper_and_no_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, memory, development, completed = [root / n for n in ('source', 'memory', 'development', 'completed')]
            for directory in (source / 'manifests', source / 'redacted', memory / 'manifests', development, completed):
                directory.mkdir(parents=True)
            inputs = {}
            for directory, names in [(source / 'manifests', ['source_manifest.json', 'split_manifest.json', 'lineage.jsonl']),
                                     (source / 'redacted', ['messages.jsonl']), (memory, ['manifest.json']),
                                     (memory / 'manifests', ['evidence-map.jsonl', 'chunks.jsonl'])]:
                for name in names:
                    path = directory / name
                    path.write_text('{}\n')
                    inputs[str(path)] = file_digest(path)
            consent = root / 'consent.json'
            consent.write_text('{}')
            inputs[str(consent)] = file_digest(consent)
            audit = {'owner_scope': 'owner-a', 'split_sha256': 'split',
                     'historical_memory_messages_by_split': {'validation': 2}}
            token = {'tokenizer': 'fixture'}
            binding = {'source_sha256': {}}
            dev_identity = {'input_digests': inputs, 'source_manifest_sha256': 'source',
                            'policy_sha256': digest(self.config), 'tokenizer': token}
            def seal(directory, identity, outputs):
                for name, content in outputs.items():
                    (directory / name).write_text(json.dumps(content))
                manifest = {'identity': identity, **validation.GOVERNANCE,
                            'output_digests': {name: file_digest(directory / name) for name in outputs}}
                manifest['manifest_sha256'] = digest(manifest)
                (directory / 'manifest.json').write_text(json.dumps(manifest))
                return manifest
            dev = seal(development, dev_identity, {'source-audit.json': audit})
            seal(completed, {'protocol': binding}, {'identity.json': {'protocol': binding},
                'report.json': {'reviewed': 400, 'status': 'complete'},
                'decisions.json': {}, 'cases.json': {}, 'train.draft.jsonl': {}})
            self.policy.update(development_manifest_sha256=file_digest(development / 'manifest.json'),
                               completed_review_manifest_sha256=file_digest(completed / 'manifest.json'))
            grouped, universe = self.group()
            args = dict(source=source, memory=memory, consent=consent, development=development,
                        completed_review=completed, config_path=ROOT / 'configs/topic-validation-v1.yaml',
                        tokenizer_path=root / 'tokenizer', output_root=root / 'outputs', controlled_root=root,
                        base_url='https://fixture.invalid', authorization_reference='synthetic-test')
            transformer = types.SimpleNamespace(AutoTokenizer=types.SimpleNamespace(from_pretrained=lambda *a, **kw: self.tokenizer))
            with patch.object(validation, 'load_policy', return_value=self.policy), \
                 patch.object(validation, 'verified_pilot', return_value=(dev, [])), \
                 patch.object(validation, 'source_identity', return_value=({'manifest_sha256': 'source'}, universe, audit, {})), \
                 patch.object(validation, 'tokenizer_identity', return_value=token), \
                 patch.object(validation, 'load_validation_days', return_value=(grouped, ['2024-05-20'], {})), \
                 patch.object(validation.axes, 'binding_for', return_value=binding), \
                 patch.dict(sys.modules, {'transformers': transformer}), \
                 patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
                planned = validation.prepare_validation(**args)
                self.assertFalse(args['output_root'].exists())
                built = validation.prepare_validation(**args, execute=True)
                self.assertEqual(built['workspace'], planned['workspace'])
                self.assertFalse(built['report']['candidate_population_complete'])
                self.assertFalse(built['report']['quality_gate_passed'])
                self.assertIn('explicit_reference', built['report']['insufficient_strata'])
                output = Path(built['workspace'])
                before = {p.name: file_digest(p) for p in output.iterdir()}
                self.assertEqual(validation.prepare_validation(**args, execute=True)['status'], 'already_built')
                self.assertEqual(before, {p.name: file_digest(p) for p in output.iterdir()})
                self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir()))
                (output / 'report.json').write_text('{}')
                with self.assertRaises(TopicContractError):
                    validation.prepare_validation(**args, execute=True)
                (source / 'redacted/messages.jsonl').write_text('changed')
                with self.assertRaisesRegex(TopicContractError, 'source dependency'):
                    validation.prepare_validation(**args)


if __name__ == '__main__':
    unittest.main()
