from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from codex_agent.catalog import Catalog
from codex_agent.registry import ToolContext, ToolRegistry
from codex_agent.state import AtomicJsonStore, OperationJournal, single_writer_lock

ROOT = Path(__file__).resolve().parents[1]


class RecoveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = AtomicJsonStore(Path(self.tmp.name))
        self.journal = OperationJournal(self.store)
        self.catalog = Catalog.load(ROOT / 'config/contracts')
        self.calls = []
        self.result = {'safe': True}
        def handler(*args):
            self.calls.append(args)
            return self.result
        self.registry = ToolRegistry(self.catalog, self.journal, {name: handler for name in self.catalog.schemas})
        self.context = ToolContext('s', 't', 'turn', 'call', 'p', actions=frozenset({'*'}))

    def test_paths_reject_traversal_absolute_and_symlink(self):
        for path in ('../escape.json', '/tmp/escape.json'):
            with self.assertRaises(ValueError):
                self.store.write(path, {})
        (self.store.root / 'link').symlink_to('/tmp', target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.read('link/escape.json')

    def test_persisted_intent_is_unknown_and_never_reexecuted(self):
        self.registry.call(self.context, 'galatea_get_capabilities', {})
        self.journal.transition('call', 'intent_recorded')
        result = self.registry.call(self.context, 'galatea_get_capabilities', {})
        self.assertEqual(result['error']['state_changed'], 'unknown')
        self.assertEqual(len(self.calls), 1)

    def test_scope_is_enforced_before_handler(self):
        result = self.registry.call(self.context, 'galatea_inspect_project', {'project_id': 'outside'})
        self.assertEqual(result['error']['category'], 'forbidden')
        self.assertFalse(self.calls)

    def test_large_results_fail_without_silent_truncation(self):
        self.result = {'value': 'a' * (257 * 1024)}
        result = self.registry.call(self.context, 'galatea_get_capabilities', {})
        self.assertEqual(result['error']['category'], 'response-too-large')
        self.assertEqual(self.journal.get('call')['state'], 'unknown')

    def test_lock_excludes_second_writer_and_stale_file_is_reusable(self):
        path = self.store.root / '.lock'
        path.write_text('stale pid')
        with single_writer_lock(path):
            with self.assertRaises(RuntimeError):
                with single_writer_lock(path):
                    pass
        with single_writer_lock(path):
            pass
