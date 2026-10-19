import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import test_release_gate
from codex_agent.config import AgentConfig
from codex_agent.cli import main


class ConsoleConfigTests(unittest.TestCase):
    def raw(self):
        raw = test_release_gate.ReleaseGateTests().raw()
        raw['paths'].pop('event_dir', None)
        return raw

    def test_default_memory_listener_and_explicit_override(self):
        config = AgentConfig.from_dict(self.raw(), Path('/tmp/config'))
        self.assertEqual((config.console_host, config.console_port), ('0.0.0.0', 18880))
        self.assertFalse(hasattr(config, 'event_dir'))
        raw = self.raw()
        raw['console'] = {'host': '192.168.1.2', 'port': 19999, 'storage': {'backend': 'memory', 'retention': 'process_lifetime'}}
        config = AgentConfig.from_dict(raw, Path('/tmp/config'))
        self.assertEqual((config.console_host, config.console_port), ('192.168.1.2', 19999))

    def test_unimplemented_storage_and_removed_fields_are_explicit_errors(self):
        for console in ({'storage': {'backend': 'redis'}}, {'storage': {'retention': 'ttl'}}, {'token_file': '/token'},
                        {'allowed_origins': []}, {'port': True}, {'port': 0}, {'host': 'bad\nExecStart=x'}):
            raw = self.raw(); raw['console'] = console
            with self.subTest(console=console), self.assertRaises(ValueError):
                AgentConfig.from_dict(raw, Path('/tmp/config'))
        raw = self.raw(); raw['paths']['event_dir'] = 'events'
        with self.assertRaisesRegex(ValueError, 'invalid paths fields'):
            AgentConfig.from_dict(raw, Path('/tmp/config'))

    def test_cli_overrides_config_without_bypassing_release_check(self):
        config = AgentConfig.from_dict(self.raw(), Path('/tmp/config'))
        with patch('codex_agent.cli.load_config', return_value=(config, config.raw)), patch('codex_agent.cli.check') as check, \
             patch('codex_agent.app.build_host', return_value=(object(), object())), patch('uvicorn.run') as run:
            main(['--config', '/tmp/config/agent.json', 'serve', '--host', '10.1.2.3', '--port', '19000'])
            check.assert_called_once()
            self.assertEqual(run.call_args.kwargs['host'], '10.1.2.3')
            self.assertEqual(run.call_args.kwargs['port'], 19000)
