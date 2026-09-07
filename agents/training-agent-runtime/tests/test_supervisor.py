import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

class SupervisorTests(unittest.TestCase):
    def test_environment_is_clean_and_timeout_reaps_group(self):
        self.assertIsNotNone(importlib.util.find_spec('training_agent.supervisor'))
        from training_agent.supervisor import Supervisor
        with tempfile.TemporaryDirectory() as directory:
            supervisor=Supervisor(Path(directory), {'PATH':os.environ['PATH'],'HOME':directory},timeout=.1,grace=.1)
            result=supervisor.execute([sys.executable,'-c','import os,json; print(json.dumps(dict(os.environ)))'])
            self.assertNotIn('AWS_SECRET_ACCESS_KEY',result.stdout)
            with self.assertRaises(TimeoutError): supervisor.execute([sys.executable,'-c','import time; time.sleep(30)'])
            supervisor.ensure_clean()
            self.assertFalse((Path(directory)/'worker.json').exists())

    def test_live_orphan_blocks_replacement(self):
        self.assertIsNotNone(importlib.util.find_spec('training_agent.supervisor'))
        from training_agent.supervisor import Supervisor
        with tempfile.TemporaryDirectory() as directory:
            supervisor=Supervisor(Path(directory),{})
            (Path(directory)/'worker.json').write_text('{"pgid":'+str(os.getpgrp())+'}')
            with self.assertRaises(RuntimeError): supervisor.ensure_clean()

    def test_cancellation_requests_interrupt_before_deadline(self):
        from training_agent.supervisor import Supervisor
        with tempfile.TemporaryDirectory() as directory:
            supervisor=Supervisor(Path(directory),{'PATH':os.environ['PATH']},timeout=10,grace=.1)
            self.assertTrue(hasattr(supervisor,'cancel_check'),'supervisor cancellation polling missing')
            supervisor.cancel_check=lambda:True
            with self.assertRaises(InterruptedError):
                supervisor.execute([sys.executable,'-c','import time; time.sleep(30)'])
            supervisor.ensure_clean()
