import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

class RequestWorkerTests(unittest.TestCase):
    def test_register_duplicate_response_revision_and_cancel(self):
        self.assertIsNotNone(importlib.util.find_spec('training_agent.requests'))
        from training_agent.requests import register,respond,cancel
        from training_agent.state import Store
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); skill=root/'SKILL.md'; skill.write_text('skill')
            lock=root/'lock.json'; lock.write_text('{}')
            store=Store(root/'state')
            register(store,'p','c',1,root,skill,lock,'request')
            with self.assertRaises(ValueError): register(store,'p','c',1,root,skill,lock,'duplicate')
            with self.assertRaises(ValueError): respond(store,'c',2,'bad')
            state=store.load('c'); state['runner_state']='awaiting_input'; store.save('c',state)
            respond(store,'c',1,'answer')
            self.assertEqual(store.load('c')['user_response'],'answer')
            cancel(store,'c')
            self.assertTrue(store.load('c')['cancel_requested'])

    def test_worker_identity_is_durable_before_effectful_run(self):
        self.assertIsNotNone(importlib.util.find_spec('training_agent.runtime.worker'))
        from training_agent.runtime.worker import run
        from training_agent.runtime.types import TurnOutcome
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); payload=root/'request.json'; result=root/'result.json'; identity=root/'identity.json'
            payload.write_text(json.dumps(dict(campaign_id='c',request_revision=1,decision_seq=1,workspace=directory,thread_id=None,skill_path=str(root/'SKILL.md'),prompt='request',output_schema={})))
            class Adapter:
                def run_turn(self,request,save):
                    save('t',None)
                    self_test.assertEqual(json.loads(identity.read_text())['thread_id'],'t')
                    save('t','u')
                    self_test.assertEqual(json.loads(identity.read_text())['turn_id'],'u')
                    return TurnOutcome('t','u','failed')
                def interrupt(self): pass
            self_test=self
            run(payload,result,identity,Adapter())
            self.assertEqual(json.loads(result.read_text())['status'],'failed')

    def test_inbox_response_and_cancel_work_while_runner_holds_lock(self):
        from training_agent import requests
        self.assertTrue(hasattr(requests,'enqueue'),'durable inbox missing')
        from training_agent.runner import initial_state
        from training_agent.state import Store
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)); state=initial_state('p','c',1,directory,'skill','lock','request')
            state['runner_state']='awaiting_input'; store.save('c',state)
            with store.lock():
                requests.enqueue(store,'c',{'kind':'respond','revision':1,'response':'answer'})
                requests.consume_inbox(store,'c')
                self.assertEqual(store.load('c')['user_response'],'answer')
                requests.enqueue(store,'c',{'kind':'cancel'})
                requests.consume_inbox(store,'c')
                self.assertTrue(store.load('c')['cancel_requested'])

    def test_cli_preflight_detects_unaccepted_runtime_without_model(self):
        self.assertIsNotNone(importlib.util.find_spec('training_agent.cli'))
        from training_agent.cli import preflight_local
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); skill=root/'SKILL.md'; skill.write_text('skill')
            lock=root/'lock.json'; lock.write_text('{"acceptance_status":"pending"}')
            with self.assertRaises(ValueError): preflight_local({'skill_path':str(skill),'runtime_lock':str(lock),'workspace':directory})

    def test_skill_binding_covers_entire_read_only_bundle(self):
        from training_agent.requests import bundle_digest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'SKILL.md').write_text('skill')
            (root / 'references').mkdir()
            reference = root / 'references' / 'contract.md'
            reference.write_text('v1')
            before = bundle_digest(root / 'SKILL.md')
            reference.write_text('v2')
            self.assertNotEqual(before, bundle_digest(root / 'SKILL.md'))

    def test_preflight_attests_runtime_executable_bytes(self):
        from unittest.mock import patch
        from training_agent.cli import preflight_local
        from training_agent.requests import bundle_digest, digest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_root = root / 'skill'; skill_root.mkdir()
            skill = skill_root / 'SKILL.md'; skill.write_text('skill')
            runtime = root / 'codex'; runtime.write_text('runtime bytes')
            lock = root / 'lock.json'
            lock.write_text(json.dumps({
                'acceptance_status': 'accepted', 'sdk_version': '1.0',
                'runtime_version_output': 'codex 1.0',
                'runtime_artifact_sha256': digest(runtime),
                'skill_bundle_sha256': bundle_digest(skill)}))
            config = {'skill_path': str(skill), 'runtime_lock': str(lock),
                      'workspace': directory, 'codex_bin': str(runtime)}
            completed = type('Completed', (), {'stdout': 'codex 1.0\n'})()
            with patch('training_agent.cli.importlib.metadata.version', return_value='1.0'), \
                 patch('training_agent.cli.subprocess.run', return_value=completed):
                self.assertEqual(preflight_local(config)['status'], 'ready')
            runtime.write_text('tampered')
            with patch('training_agent.cli.importlib.metadata.version', return_value='1.0'), \
                 patch('training_agent.cli.subprocess.run', return_value=completed):
                with self.assertRaisesRegex(ValueError, 'artifact digest'):
                    preflight_local(config)

    def test_skill_digest_includes_sibling_skills_and_contracts(self):
        from training_agent.requests import bundle_digest
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'skill-lock.json').write_text('{}')
            entry=root/'skills'/'training-campaign'; entry.mkdir(parents=True)
            skill=entry/'SKILL.md'; skill.write_text('entry')
            sibling=root/'skills'/'model-delivery';sibling.mkdir()
            reference=sibling/'SKILL.md';reference.write_text('one')
            before=bundle_digest(skill)
            reference.write_text('two')
            self.assertNotEqual(bundle_digest(skill),before)
