from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from codex_agent.deployment import render_unit


class DeploymentRenderTests(unittest.TestCase):
    def test_unit_allows_only_declared_state_and_protects_runtime_config(self):
        config=SimpleNamespace(config_dir=Path('/var/lib/galatea-agent'),codex_home=Path('/var/lib/galatea-agent/codex-home'),
            state_dir=Path('/var/lib/galatea-agent/state'),event_dir=Path('/var/lib/galatea-agent/events'),
            workspace_dir=Path('/var/lib/galatea-agent/workspace'),
            raw={'galatea':{'state_root':'/var/lib/galatea-mcp','platform':{'artifact_download_root':'/var/lib/galatea-mcp/downloads'}}})
        unit=render_unit(config,executable=Path('/opt/agent/bin/codex-agent'),user='galatea-mcp',
            token_file=Path('/etc/agent/token'),environment_file=Path('/etc/agent/host.env'),conflicts='galatea-mcp.service')
        self.assertIn('Conflicts=galatea-mcp.service',unit)
        self.assertIn('ReadWritePaths=/var/lib/galatea-mcp',unit)
        self.assertIn('ReadOnlyPaths=/var/lib/galatea-agent/codex-home/config.toml',unit)
        self.assertNotIn('ReadWritePaths=/var/lib/galatea-agent\n',unit)
        self.assertIn('KillMode=control-group',unit)
        self.assertIn('UMask=0077',unit)
        self.assertIn('ProtectProc=invisible',unit)
        self.assertIn('Environment=PYTHONNOUSERSITE=1',unit)
        self.assertIn('UnsetEnvironment=PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE PYTHONINSPECT',unit)

    def test_systemd_injection_in_paths_or_user_is_rejected(self):
        config=SimpleNamespace(config_dir=Path('/tmp/state\nExecStart=/evil'))
        with self.assertRaises(ValueError):
            render_unit(config,executable=Path('/opt/agent'),user='root\nUser=other',
                        token_file=Path('/etc/token'),environment_file=Path('/etc/env'))
