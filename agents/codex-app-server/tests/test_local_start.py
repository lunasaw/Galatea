import importlib.util
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
sys.path.insert(0, str(ROOT.parents[1] / 'services/galatea-mcp/src'))
from codex_agent.config import AgentConfig
from codex_agent.local import build_local_service, prepare, source_manifest
from codex_agent.stage0 import GateError

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
        self.env = patch.dict(os.environ, {'GALATEA_AUTH_DIR': str(self.auth)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.binary = Path('/usr/bin/true').resolve()
        bundled = patch('codex_agent.local.subprocess.check_output',
            return_value=json.dumps({'models': [{'slug': 'fixture', 'tool_mode': 'code_mode'}]}))
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

    def test_source_checks_runtime_and_restricts_unconfigured_platform(self):
        config = self.config()
        with self.assertRaises(GateError):
            source_manifest(config)
        service, principal = build_local_service(config)
        self.assertIsNotNone(service.call(principal, 'galatea_get_capabilities', {}))
        raw = json.loads(json.dumps(config.raw))
        raw['galatea']['principal']['actions'].append('galatea_submit')
        restricted = AgentConfig.from_dict(raw, config.config_dir)
        with self.assertRaises(ValueError):
            build_local_service(restricted)

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
