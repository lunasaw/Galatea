import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        from galatea_mcp.state import StateStore
        self.store = StateStore(Path(self.temp.name).resolve())
        self.addCleanup(self.store.close)

    def test_atomic_failure_preserves_old_snapshot(self):
        self.store.save('campaigns', 'c1', {'schema_version': 'galatea.state/v1', 'value': 1})
        with patch('os.replace', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                self.store.save('campaigns', 'c1', {'schema_version': 'galatea.state/v1', 'value': 2})
        self.assertEqual(self.store.read('campaigns', 'c1')['value'], 1)
        self.assertEqual(len(list((Path(self.temp.name) / 'campaigns').iterdir())), 1)

    def test_second_writer_cannot_open(self):
        from galatea_mcp.state import StateStore
        from galatea_mcp.errors import DomainError
        with self.assertRaisesRegex(DomainError, 'writer-locked'):
            StateStore(Path(self.temp.name).resolve())

    def test_corruption_and_symlink_fail_closed(self):
        from galatea_mcp.errors import DomainError
        self.store.save('campaigns', 'c1', {'schema_version': 'galatea.state/v1'})
        path = Path(self.temp.name) / 'campaigns' / 'c1.json'
        path.write_text('{')
        with self.assertRaisesRegex(DomainError, 'state-corrupt'):
            self.store.read('campaigns', 'c1')
        path.unlink()
        path.symlink_to(Path(self.temp.name) / 'writer.lock')
        with self.assertRaisesRegex(DomainError, 'unsafe-path'):
            self.store.read('campaigns', 'c1')
        with self.assertRaises(DomainError):
            self.store.read('campaigns', '../outside')

    def test_marker_is_exclusive_and_never_overwritten(self):
        from galatea_mcp.errors import DomainError
        original = {'schema_version': 'galatea.state/v1', 'operation_id': 'op1'}
        self.store.claim('holdout1', original)
        self.store.claim('holdout1', original)
        with self.assertRaisesRegex(DomainError, 'holdout-used'):
            self.store.claim('holdout1', {**original, 'operation_id': 'op2'})
        self.assertEqual(self.store.read('evaluation-uses', 'holdout1'), original)

    def test_digest_has_fixed_canonical_vector_and_rejects_nonfinite(self):
        from galatea_mcp.state import digest
        self.assertEqual(digest({'schema_version': 'v1', 'b': 2, 'a': 1}),
                         'e0ba61c510c562b69788d1a663d94e919bba6b73aeab7f2264cef131410badfc')
        with self.assertRaises((ValueError, TypeError)):
            digest({'n': float('nan')})
