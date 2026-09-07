import importlib.util
import tempfile
import unittest
from pathlib import Path

class RunnerTests(unittest.TestCase):
    def test_approved_platform_revision_resumes_existing_registration(self):
        from training_agent.runner import Runner, initial_state
        from training_agent.state import Store
        from training_agent.runtime.types import TurnOutcome

        class Platform:
            def snapshot(self, state):
                return ({'request_revision': 2, 'cancelled': False, 'report': None,
                         'budget': {'cpu_seconds': 1}}, [])

        calls = []
        class Runtime:
            def run_turn(self, request, save):
                calls.append(request)
                return TurnOutcome('thread', 'turn', 'failed', usage_snapshot={
                    'thread_id': 'thread', 'total_tokens': 1})

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            state = initial_state('p', 'c', 1, directory, 'skill', 'lock', 'request')
            state.update(runner_state='awaiting_approval', user_response='approved in platform',
                         last_observation_digest='old')
            store.save('c', state)
            Runner(store, 'c', Platform(), Runtime()).tick(0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].request_revision, 2)
            self.assertNotEqual(store.load('c')['runner_state'], 'blocked')

    def test_read_only_wait_does_not_require_inference_preflight(self):
        from training_agent.runner import Runner, initial_state
        from training_agent.state import Store

        class Platform:
            def snapshot(self, state):
                return ({'request_revision': 1, 'cancelled': False, 'report': None,
                         'budget': {}},
                        [{'operation_id': 'op', 'execution': 'running'}])

        class Runtime:
            def prepare_inference(self):
                raise AssertionError('read-only observation must not require the SDK or Skill')

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.save('c', initial_state('p', 'c', 1, directory, 'skill', 'lock', 'request'))
            Runner(store, 'c', Platform(), Runtime()).tick(0)
            self.assertEqual(store.load('c')['runner_state'], 'waiting_external')

    def test_exhausted_runner_verifies_frozen_candidate_without_model(self):
        from training_agent.runner import Runner, initial_state
        from training_agent.state import Store

        class Platform:
            def __init__(self):
                self.verified = []

            def snapshot(self, state):
                return ({'request_revision': 1, 'cancelled': False,
                         'candidate': {'candidate_id': 'candidate-1'},
                         'report': None, 'budget': {}}, [])

            def verify_candidate(self, state, candidate_id):
                self.verified.append(candidate_id)
                return {'outcome': 'accepted', 'integrity': 'verified',
                        'report_ref': 'report-1', 'evidence_refs': [],
                        'final_test_status': 'passed'}

        class Runtime:
            def run_turn(self, *args):
                raise AssertionError('usage exhaustion must not invoke the model')

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            state = initial_state('p', 'c', 1, directory, 'skill', 'lock', 'request')
            state.update(usage_unknown=True, runner_state='waiting_external')
            store.save('c', state)
            platform = Platform()
            Runner(store, 'c', platform, Runtime()).tick(0)
            result = store.load('c')
            self.assertEqual(platform.verified, ['candidate-1'])
            self.assertEqual(result['runner_state'], 'completed')
            self.assertEqual(result['report_ref'], 'report-1')

    def test_terminal_operation_without_evidence_waits_without_model_churn(self):
        from training_agent.runner import Runner, initial_state
        from training_agent.state import Store

        class Platform:
            def snapshot(self, state):
                return ({'request_revision': 1, 'cancelled': False,
                         'candidate': None, 'report': None, 'budget': {}},
                        [{'operation_id': 'op', 'execution': 'succeeded',
                          'quality': 'pending', 'integrity': 'pending'}])

        class Runtime:
            def run_turn(self, *args):
                raise AssertionError('incomplete terminal evidence must only be observed')

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            state = initial_state('p', 'c', 1, directory, 'skill', 'lock', 'request')
            state.update(runner_state='waiting_external', last_observation_digest='old')
            store.save('c', state)
            runner = Runner(store, 'c', Platform(), Runtime())
            runner.tick(0)
            runner.tick(60)
            self.assertEqual(store.load('c')['runner_state'], 'waiting_evidence')

    def test_submission_crash_waits_without_new_model_and_unknown_usage_blocks(self):
        self.assertIsNotNone(importlib.util.find_spec('training_agent.runner'))
        from training_agent.runner import Runner, initial_state
        from training_agent.state import Store
        from training_agent.runtime.types import TurnOutcome
        class Platform:
            operations=[]
            def snapshot(self, state): return ({'request_revision':1,'cancelled':False,'report':None,'budget':{}}, self.operations)
        platform=Platform()
        calls=[]
        class Runtime:
            def run_turn(self, request, save):
                calls.append(request)
                save('t','u')
                platform.operations=[{'operation_id':'op','execution':'running'}]
                return TurnOutcome('t','u','failed',error='crashed')
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)); state=initial_state('p','c',1,directory,'skill','lock','request')
            store.save('c',state)
            runner=Runner(store,'c',platform,Runtime())
            runner.tick(0); runner.tick(60); runner.tick(120)
            self.assertEqual(len(calls),1)
            self.assertEqual(store.load('c')['runner_state'],'waiting_external')
            self.assertEqual(store.load('c')['operation_ids'],['op'])
            platform.operations[0]['execution']='succeeded'
            runner.tick(180)
            self.assertEqual(len(calls),1)
            self.assertEqual(store.load('c')['runner_state'],'waiting_evidence')

    def test_wait_then_resume_and_verified_delivery(self):
        self.assertIsNotNone(importlib.util.find_spec('training_agent.runner'))
        from training_agent.runner import Runner, initial_state
        from training_agent.state import Store
        from training_agent.runtime.types import TurnOutcome
        class Platform:
            operations=[]
            report=None
            def snapshot(self,state): return ({'request_revision':1,'cancelled':False,'report':self.report,'budget':{}},self.operations)
        platform=Platform(); calls=[]
        class Runtime:
            def run_turn(self, request, save):
                calls.append(request); save('t',str(len(calls)))
                if len(calls)==1: platform.operations=[{'operation_id':'op','execution':'running'}]
                output=dict(schema_version='galatea.agent-turn/v1',campaign_id='c',request_revision=1,decision_seq=len(calls),action='wait_external' if len(calls)==1 else 'complete',operation_ids=['op'],evidence_refs=[],proposed_outcome=None if len(calls)==1 else 'accepted',reason='ok',user_input_request=None,report_ref=None if len(calls)==1 else 'r')
                return TurnOutcome('t',str(len(calls)),'completed',output,{'thread_id':'t','total_tokens':100+60*(len(calls)-1)})
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)); store.save('c',initial_state('p','c',1,directory,'skill','lock','request'))
            runner=Runner(store,'c',platform,Runtime()); runner.tick(0); runner.tick(60)
            self.assertEqual(len(calls),1)
            platform.operations[0]['execution']='succeeded'
            platform.report=dict(report_ref='r',outcome='accepted',evidence_refs=[],integrity='verified',final_test_status='passed')
            runner.tick(120)
            self.assertEqual(calls[1].thread_id,'t')
            self.assertEqual(store.load('c')['runner_state'],'completed')
            self.assertEqual(store.load('c')['observed_tokens'],160)
