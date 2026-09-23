"""Abrupt process exits at actual intent/handler/receipt boundaries, no real backend."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from codex_agent.catalog import Catalog
from codex_agent.registry import ToolContext,ToolRegistry
from codex_agent.state import AtomicJsonStore,OperationJournal

CHILD = r'''
import os,sys
from pathlib import Path
from codex_agent.catalog import Catalog
from codex_agent.registry import ToolContext,ToolRegistry
from codex_agent.state import AtomicJsonStore,OperationJournal
root,contracts,window=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]
store=AtomicJsonStore(root/'state');journal=OperationJournal(store)
catalog=Catalog.load(contracts)
original_begin=journal.begin
original_transition=journal.transition
def begin(*args):
    result=original_begin(*args)
    if window=='intent': os._exit(91)
    return result
def transition(call_id,state,**kwargs):
    if state=='receipt_saved' and window=='receipt': os._exit(93)
    return original_transition(call_id,state,**kwargs)
journal.begin=begin;journal.transition=transition
def handler(*args):
    with (root/'external-effect').open('ab') as stream:
        stream.write(b'one effect\n');stream.flush();os.fsync(stream.fileno())
    if window=='handler': os._exit(92)
    return {'done':True}
registry=ToolRegistry(catalog,journal,{name:handler for name in catalog.schemas})
registry.call(ToolContext('s','t','turn','call','p',actions=frozenset({'*'})), 'galatea_get_capabilities',{})
'''


class CrashProcessTests(unittest.TestCase):
    def test_each_crash_window_prevents_handler_reexecution_after_restart(self):
        catalog=Catalog.load(ROOT/'config/contracts')
        for window,code in (('intent',91),('handler',92),('receipt',93)):
            with self.subTest(window=window),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                result=subprocess.run([sys.executable,'-c',CHILD,directory,str(ROOT/'config/contracts'),window],
                    env={**os.environ,'PYTHONPATH':str(ROOT/'src')},capture_output=True,timeout=10)
                self.assertEqual(result.returncode,code,result.stderr.decode())
                calls=[]
                registry=ToolRegistry(catalog,OperationJournal(AtomicJsonStore(root/'state')),
                    {name:lambda *_:calls.append(True) for name in catalog.schemas})
                result=registry.call(ToolContext('s','t','turn','call','p',actions=frozenset({'*'})),
                                     'galatea_get_capabilities',{})
                self.assertEqual(result['error']['state_changed'],'unknown')
                self.assertFalse(calls)
                if window!='intent':
                    self.assertEqual((root/'external-effect').read_bytes(),b'one effect\n')
