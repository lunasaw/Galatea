"""An upgrade may rebind idle sessions, never widen their identity or grants."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from codex_agent.catalog import digest
from codex_agent.config import AgentConfig
from codex_agent.runtime_upgrade import migrate_source_sessions
from codex_agent.runtime_version import RUNTIME_VERSION
from codex_agent.stage0 import file_sha256
from codex_agent.state import AtomicJsonStore, payload_digest


class RuntimeUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        previous = json.loads((ROOT / 'config/agent.example.json').read_text())
        previous['codex'].update(binary='/usr/bin/true', runtime_version='codex-cli 0.154.0')
        self.previous = previous
        (self.root / 'agent.before-runtime-0.163.0.json').write_text(json.dumps(previous))
        current = copy.deepcopy(previous)
        current['codex'].update(binary='/usr/bin/false', runtime_version=RUNTIME_VERSION)
        self.config = AgentConfig.from_dict(current, self.root)
        self.store = AtomicJsonStore(self.config.state_dir)
        self.bindings = {'runtime_binary_sha256': 'new-binary', 'runtime_compatibility_sha256': 'new-protocol',
            'agent_config_sha256': digest(current), 'effective_config_sha256': 'new-effective',
            'extensions_sha256': 'extensions', 'catalog_digest': self.config.catalog().digest,
            'principal_digest': 'principal'}
        self.session = {'id': 's', 'thread_id': 'native-t', 'status': 'recovery_required',
            'active_turn_id': None, 'active_request_id': None, 'settings': {'approvalPolicy': 'on-request'},
            **self.bindings, 'runtime_binary_sha256': file_sha256(Path('/usr/bin/true')),
            'runtime_compatibility_sha256': digest({'protocol': file_sha256(ROOT / 'src/codex_agent/protocol/0.154.0.json'),
                                                  'catalog': self.config.catalog().digest}),
            'agent_config_sha256': digest(previous), 'effective_config_sha256': 'old-effective'}
        mock = patch('codex_agent.runtime_upgrade.subprocess.check_output', return_value='codex-cli 0.154.0')
        mock.start()
        self.addCleanup(mock.stop)

    def migrate(self):
        migrate_source_sessions(self.config, self.store, self.bindings)
        return self.store.read('sessions/s.json')

    def test_idle_session_keeps_identity_settings_and_original_evidence(self):
        self.store.write('sessions/s.json', self.session)
        updated = self.migrate()
        self.assertEqual(updated, {**self.session, **self.bindings})
        evidence = self.store.read('runtime-upgrades/0.163.0/s.json')
        self.assertEqual(evidence['previous_session'], self.session)
        self.assertEqual(self.migrate(), updated)
        self.assertEqual(self.store.read('runtime-upgrades/0.163.0/s.json'), evidence)

    def test_busy_or_mismatched_sessions_remain_blocked(self):
        for change in ({'active_turn_id': 'turn'}, {'active_request_id': 'request'}, {'status': 'starting'},
                       *({key: 'changed'} for key in self.bindings if key != 'effective_config_sha256')):
            with self.subTest(change=change):
                original = {**self.session, **change}
                self.store.write('sessions/s.json', original)
                self.assertEqual(self.migrate(), original)
                self.assertIsNone(self.store.read('runtime-upgrades/0.163.0/s.json'))

    def test_unresolved_tool_or_request_prevents_migration(self):
        self.store.write('sessions/s.json', self.session)
        self.store.write('tool-calls/c.json', {'identity': {'session_id': 's'}, 'state': 'executing'})
        self.assertEqual(self.migrate(), self.session)
        self.store.write('tool-calls/c.json', {'identity': {'session_id': 's'}, 'state': 'receipt_saved'})
        self.store.write('requests/r.json', {'session_id': 's', 'status': 'unknown'})
        self.assertEqual(self.migrate(), self.session)
        self.store.write('requests/r.json', {'session_id': 's', 'status': 'completed'})
        self.assertEqual(self.migrate(), {**self.session, **self.bindings})

    def test_unrelated_configuration_change_cannot_use_upgrade_backup(self):
        self.store.write('sessions/s.json', self.session)
        changed = copy.deepcopy(self.config.raw)
        changed['agent']['id'] = 'other-principal'
        self.config = AgentConfig.from_dict(changed, self.root)
        self.assertEqual(self.migrate(), self.session)


class SourceExtensionUpgradeTests(RuntimeUpgradeTests):
    def setUp(self):
        super().setUp()
        self.session.update(self.bindings)
        self.session['extensions_sha256'] = 'old-extensions'
        self.session['principal_digest'] = payload_digest([self.config.principal_id, sorted(self.config.actions)])
        self.store.write('sessions/s.json', self.session)
        mock = patch('codex_agent.local.source_manifest', return_value={**self.bindings, 'extensions_sha256': 'new-extensions'})
        mock.start()
        self.addCleanup(mock.stop)

    def migrate(self):
        from codex_agent.runtime_upgrade import rebind_source_extensions
        return rebind_source_extensions(self.config, 's', expected_previous='old-extensions', expected_current='new-extensions')

    def test_idle_session_keeps_identity_settings_and_original_evidence(self):
        result = self.migrate()
        self.assertEqual(result, {**self.session, 'status': 'recovery_required', 'extensions_sha256': 'new-extensions'})
        self.assertEqual(self.migrate(), result)
        self.assertEqual(self.store.read('source-upgrades/new-extensions/s.json')['previous_session'], self.session)

    def test_busy_or_mismatched_sessions_remain_blocked(self):
        for change in ({'active_turn_id': 'turn'}, {'active_request_id': 'request'}, {'status': 'running'},
                       {'extensions_sha256': 'unreviewed'}, {'principal_digest': 'other'}, {'catalog_digest': 'other'},
                       {'agent_config_sha256': 'other'}, {'runtime_binary_sha256': 'other'}):
            with self.subTest(change=change):
                original = {**self.session, **change}
                self.store.write('sessions/s.json', original)
                with self.assertRaises(ValueError): self.migrate()
                self.assertEqual(self.store.read('sessions/s.json'), original)

    def test_unresolved_tool_or_request_prevents_migration(self):
        self.store.write('requests/r.json', {'session_id': 's', 'status': 'unknown'})
        with self.assertRaises(ValueError): self.migrate()
        self.store.write('requests/r.json', {'session_id': 's', 'status': 'completed'})
        self.store.write('tool-calls/c.json', {'identity': {'session_id': 's'}, 'state': 'executing'})
        with self.assertRaises(ValueError): self.migrate()

    def test_unrelated_configuration_change_cannot_use_upgrade_backup(self):
        from codex_agent.state import single_writer_lock
        with single_writer_lock(self.config.state_dir / '.host.lock'):
            with self.assertRaises(RuntimeError): self.migrate()
        with patch('codex_agent.local.source_manifest', return_value={**self.bindings, 'extensions_sha256': 'not-reviewed'}):
            with self.assertRaises(ValueError): self.migrate()
