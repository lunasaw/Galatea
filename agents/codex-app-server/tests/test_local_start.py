import importlib.util
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from codex_agent.config import AgentConfig
from codex_agent.local import prepare, source_manifest
from codex_agent.stage0 import GateError
from codex_agent.runtime_version import RUNTIME_VERSION

spec = importlib.util.spec_from_file_location('local_process', ROOT / 'scripts/local_process.py')
process = importlib.util.module_from_spec(spec)
spec.loader.exec_module(process)


class SourceStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'project'
        shutil.copytree(ROOT / 'config', self.project / 'config')
        self.auth = Path(self.temp.name) / 'auth'
        self.auth.mkdir()
        (self.auth / 'auth.json').write_text('{"OPENAI_API_KEY":"fixture"}')
        (self.auth / 'config.toml').write_text('model = "fixture"\nmodel_provider = "custom"\n'
            '[model_providers.custom]\nname = "Custom"\nbase_url = "http://localhost/v1"\n'
            'http_headers = {"X-Test" = "example"}\n')
        self.env = patch.dict(os.environ, {'CODEX_AGENT_AUTH_DIR': str(self.auth)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.binary = Path('/usr/bin/true').resolve()
        bundled = patch('codex_agent.local.subprocess.check_output',
            side_effect=lambda args, **kwargs: RUNTIME_VERSION if args[1:] == ['--version'] else
                json.dumps({'models': [{'slug': 'fixture', 'tool_mode': 'code_mode'}]}))
        bundled.start()
        self.addCleanup(bundled.stop)

    def config(self):
        prepare(self.project, self.binary)
        path = self.project / '.local/agent.json'
        raw = json.loads(path.read_text())
        return AgentConfig.from_dict(raw, path.parent)

    def test_bootstrap_is_local_preserves_config_and_protects_auth(self):
        config = self.config()
        self.assertEqual(config.codex_home, self.project / '.local/codex-home')
        self.assertNotIn('release', config.raw)
        path = config.codex_home / 'config.toml'
        settings = tomllib.loads(path.read_text())
        self.assertEqual(settings['model'], 'fixture')
        models = json.loads(Path(settings['model_catalog_json']).read_text())
        self.assertEqual(models['models'][0]['tool_mode'], 'direct')
        self.assertEqual(settings['model_providers']['custom']['http_headers']['X-Test'], 'example')
        self.assertEqual((config.codex_home / 'auth.json').stat().st_mode & 0o777, 0o600)
        changed = path.read_text().replace('model = "fixture"', 'model = "user-edit"')
        path.write_text(changed)
        self.config()
        self.assertEqual(path.read_text(), changed)

    def test_source_checks_runtime_without_any_business_configuration(self):
        config = self.config()
        self.assertEqual(config.catalog().schemas, {})
        self.assertEqual(config.providers, ())
        self.assertNotIn('galatea', config.raw)
        self.assertFalse((config.config_dir / 'platform-state').exists())
        self.assertIn('runtime_compatibility_sha256', source_manifest(config))

    def test_upgrade_preserves_state_settings_and_backs_up_previous_config(self):
        config = self.config()
        path = config.config_dir / 'agent.json'
        raw = json.loads(path.read_text())
        raw['codex'].update(runtime_version='codex-cli 0.154.0', binary='/old/runtime/bin/codex')
        raw['paths'].update(runtime_dir='/old/runtime', codex_home='custom-home', state_dir='custom-state')
        raw['agent']['max_active_turns'] = 7
        path.write_text(json.dumps(raw))
        updated = self.config()
        self.assertEqual(updated.runtime_version, RUNTIME_VERSION)
        self.assertEqual(updated.binary, self.binary)
        self.assertEqual(updated.codex_home, config.config_dir / 'custom-home')
        self.assertEqual(updated.state_dir, config.config_dir / 'custom-state')
        self.assertEqual(updated.raw['agent'], raw['agent'])
        backup = config.config_dir / 'agent.before-runtime-0.163.0.json'
        self.assertEqual(json.loads(backup.read_text()), raw)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.config()
        self.assertEqual(json.loads(backup.read_text()), raw)

    def test_unsupported_binary_does_not_change_existing_config(self):
        config = self.config()
        path = config.config_dir / 'agent.json'
        previous = path.read_bytes()
        with patch('codex_agent.local.subprocess.check_output', return_value='codex-cli 0.154.0'):
            with self.assertRaises(GateError):
                prepare(self.project, self.binary)
        self.assertEqual(path.read_bytes(), previous)

    def test_source_rejects_a_runtime_outside_the_supported_protocol(self):
        config = replace(self.config(), runtime_version='codex-cli 0.153.4')
        with patch('codex_agent.local.subprocess.check_output', return_value=config.runtime_version):
            with self.assertRaisesRegex(GateError, 'supported Codex'):
                source_manifest(config)

    def test_restart_kills_only_recorded_project_and_children(self):
        directory = self.project / '.local'
        directory.mkdir()
        command = [sys.executable, '-c',
            'import subprocess,time; subprocess.Popen(["/bin/sleep","60"]); time.sleep(60)',
            'codex_agent.cli', str(directory / 'agent.json')]
        supervisor = subprocess.Popen([sys.executable, str(ROOT / 'scripts/local_process.py'),
            'run', str(self.project), *command])
        unrelated = subprocess.Popen(['/bin/sleep', '60'])
        self.addCleanup(unrelated.wait)
        self.addCleanup(lambda: unrelated.poll() is None and unrelated.kill())
        try:
            deadline = time.monotonic() + 5
            while not (directory / 'console.pid').exists() and time.monotonic() < deadline:
                time.sleep(.02)
            record = json.loads((directory / 'console.pid').read_text())
            children = []
            while not children and time.monotonic() < deadline:
                children = process.descendants(record['pid'])
                time.sleep(.02)
            self.assertTrue(children)
            process.stop(self.project)
            self.assertIsNone(process.identity(record['pid']))
            self.assertTrue(all(process.identity(c['pid']) is None for c in children))
            self.assertIsNone(unrelated.poll())
            self.assertEqual(supervisor.wait(timeout=5), 143)
            self.assertFalse((directory / 'console.pid').exists())
        finally:
            if supervisor.poll() is None:
                process.stop(self.project)
                supervisor.wait(timeout=5)

    def test_pid_file_cannot_stop_an_unrelated_process(self):
        directory = self.project / '.local'
        directory.mkdir()
        sleeper = subprocess.Popen(['/bin/sleep', '60'])
        try:
            (directory / 'console.pid').write_text(json.dumps(process.identity(sleeper.pid)))
            with self.assertRaises(SystemExit):
                process.stop(self.project)
            self.assertIsNone(sleeper.poll())
            stale = {'pid': sleeper.pid, 'started': 'wrong-start-time'}
            (directory / 'console.pid').write_text(json.dumps(stale))
            process.stop(self.project)
            self.assertIsNone(sleeper.poll())
        finally:
            sleeper.terminate()
            sleeper.wait()


if __name__ == '__main__':
    unittest.main()
