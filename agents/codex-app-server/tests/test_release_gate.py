import json
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from codex_agent.cli import main
from codex_agent.config import AgentConfig
from codex_agent.stage0 import GateError


class ReleaseGateTests(unittest.TestCase):
    def raw(self):
        return {'schema_version':'codex-agent/v1', 'paths':{
            'codex_home':'codex-home','runtime_dir':'runtime','state_dir':'state','event_dir':'events',
            'workspace_dir':'workspace','contracts':str(ROOT / 'config/contracts/tools.json'),
            'catalog_metadata':str(ROOT / 'config/contracts/catalog-metadata.json')},
            'codex':{'binary':'runtime/bin/codex','runtime_version':'codex-cli 0.153.4'},
            'galatea':{'principal':{'principal_id':'p','actions':['galatea_get_capabilities']}}}

    def test_serve_cannot_bypass_release_check_or_construct_service_first(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'agent.json'
            config.write_text(json.dumps(self.raw()))
            with patch('codex_agent.platform.build_service') as factory:
                with self.assertRaises((GateError, ValueError)):
                    main(['--config',str(config),'serve','--token-file',str(config)])
                factory.assert_not_called()

    def test_production_cli_rejects_alternate_unmeasured_service_factory(self):
        with patch('codex_agent.cli.load_config') as load, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                main(['--config','/absent/agent.json','serve',
                      '--service-factory','unsafe:factory','--token-file','/absent/token'])
            self.assertEqual(error.exception.code, 2)
            load.assert_not_called()

    def test_invalid_config_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for value in (0, -1, True, '4', 10000):
                raw = self.raw(); raw['agent'] = {'max_active_turns': value}
                with self.assertRaises(ValueError):
                    AgentConfig.from_dict(raw, root)
            raw = self.raw(); raw['unexpected'] = True
            with self.assertRaises(ValueError):
                AgentConfig.from_dict(raw, root)
            (root/'runtime').symlink_to('/tmp')
            with self.assertRaises(ValueError):
                AgentConfig.from_dict(self.raw(), root)

    def test_child_environment_does_not_inherit_path_or_backend_or_model_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AgentConfig.from_dict(self.raw(), Path(directory))
            with patch.dict('os.environ', {'PATH':'/untrusted/bin', 'OPENAI_API_KEY':'host-secret', 'MLFLOW_TRACKING_TOKEN':'backend-secret'}):
                env = config.child_environment()
                self.assertEqual(env['PATH'], '/usr/bin:/bin')
                self.assertNotIn('OPENAI_API_KEY', env)
                self.assertNotIn('backend-secret', str(env))
