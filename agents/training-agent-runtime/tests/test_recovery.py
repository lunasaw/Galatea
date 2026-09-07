import json
import tempfile
import unittest
from pathlib import Path
from training_agent.runner import Runner,initial_state
from training_agent.state import Store
from training_agent.supervisor import Supervisor

class RecoveryTests(unittest.TestCase):
    def test_started_turn_without_outcome_blocks_new_decisions(self):
        class Platform:
            def snapshot(self,state): return ({'request_revision':1,'cancelled':False,'report':None,'budget':{}},[])
        calls=[]
        class Runtime:
            def run_turn(self,*args):
                calls.append(1)
                raise AssertionError('Must not call model after lost outcome')
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)); state=initial_state('p','c',1,directory,'skill','lock','request')
            state.update(runner_state='agent_running',decision_seq=1,thread_id='t')
            store.save('c',state); Runner(store,'c',Platform(),Runtime()).tick(0)
            self.assertEqual(store.load('c')['runner_state'],'blocked')
            self.assertTrue(store.load('c')['usage_unknown'])
            self.assertEqual(calls,[])

    def test_persistent_identity_replayed_after_parent_crash(self):
        self.assertTrue(hasattr(Supervisor,'recover_identity'))
        with tempfile.TemporaryDirectory() as directory:
            supervisor=Supervisor(Path(directory),{})
            (Path(directory)/'identity.json').write_text(json.dumps({'campaign_id':'c','decision_seq':2,'thread_id':'t','turn_id':'u'}))
            state={'campaign_id':'c','decision_seq':2}
            self.assertEqual(supervisor.recover_identity(state),('t','u'))
            with self.assertRaises(ValueError): supervisor.recover_identity({'campaign_id':'other','decision_seq':2})

    def test_cancellation_stays_pending_until_operations_terminal(self):
        class Platform:
            operations=[{'operation_id':'op','execution':'unknown'}]
            cancelled=False
            def snapshot(self,state): return ({'request_revision':1,'cancelled':self.cancelled},self.operations)
            def cancel(self,state): self.cancelled=True
            def stop(self,state,operation_id): self.operations[0]['execution']='stopping'
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)); state=initial_state('p','c',1,directory,'skill','lock','request'); state['cancel_requested']=True
            store.save('c',state); platform=Platform(); runner=Runner(store,'c',platform,object())
            runner.tick(0); self.assertEqual(store.load('c')['runner_state'],'cancelling')
            platform.operations[0]['execution']='stopped'; runner.tick(60)
            self.assertEqual(store.load('c')['runner_state'],'cancelled')

    def test_verified_platform_delivery_finishes_when_usage_unknown(self):
        class Platform:
            def snapshot(self,state):
                return ({'request_revision':1,'cancelled':False,'report':{'report_ref':'report','integrity':'verified','outcome':'accepted','final_test_status':'passed','evidence_refs':[]}},[])
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)); state=initial_state('p','c',1,directory,'skill','lock','request')
            state.update(usage_unknown=True,runner_state='waiting_external')
            store.save('c',state); Runner(store,'c',Platform(),object()).tick(0)
            self.assertEqual(store.load('c')['runner_state'],'completed')
            self.assertEqual(store.load('c')['report_ref'],'report')
