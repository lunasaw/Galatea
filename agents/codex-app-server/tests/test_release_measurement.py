import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from codex_agent.config import AgentConfig
from codex_agent.catalog import digest
from codex_agent.release import verify_release
from codex_agent.stage0 import GateError, REQUIRED_CHECKS, file_sha256


class ReleaseMeasurementTests(unittest.TestCase):
    def setUp(self):
        # Permission semantics have dedicated tests; synthetic evidence need not
        # be created by root on developer machines.
        patcher=patch('codex_agent.release.require_protected_path')
        patcher.start();self.addCleanup(patcher.stop)
        patcher=patch('codex_agent.release.require_protected_tree')
        patcher.start();self.addCleanup(patcher.stop)

    def test_evidence_manifest_and_measured_artifact_must_all_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = {'schema_version': 'codex-agent/v2', 'paths': {'codex_home': 'codex-home', 'runtime_dir': 'runtime', 'state_dir': 'state', 'workspace_dir': 'workspace'}, 'codex': {'binary': 'runtime/bin/codex', 'runtime_version': 'test'}, 'release': {'manifest': 'manifest.json', 'stage0_record': 'stage0.json'}, 'agent': {'id': 'p'}, 'tools': {'catalog_dir': str(ROOT / 'tests/fixtures/dynamic-tools'), 'actions': ['galatea_get_capabilities'], 'providers': [{'path': str(ROOT / 'tests/fixtures/dynamic-tools/provider.py')}]}}
            raw['codex']['compatibility_profile'] = str(ROOT/'tests/fixtures/dynamic-tools/runtime-compatibility.json')
            config = AgentConfig.from_dict(raw, root)
            artifact = root/'fixture.json'; artifact.write_text('{"unit_test_only":true}')
            (root/'runtime/bin').mkdir(parents=True)
            (root/'runtime/bin/codex').write_text('fixture')
            runtime_sha = file_sha256(root/'runtime/bin/codex')
            manifest = {'runtime_binary_sha256':runtime_sha,'effective_config_sha256':'b'*64}
            (root/'manifest.json').write_text(json.dumps(manifest))
            record = {'status':'passed','release_sha256':digest(manifest),'checks':{
                name:{'status':'passed','evidence_path':'fixture.json','evidence_sha256':file_sha256(artifact)}
                for name in REQUIRED_CHECKS}}
            provenance = {'schema_version':'codex-agent.runtime-provenance/v1','status':'passed',
                'verification':'npm-audit-signatures-and-sigstore-attestations',
                'source_repository':'https://github.com/openai/codex','runtime_binary_sha256':runtime_sha}
            proof=root/'provenance.json';proof.write_text(json.dumps(provenance))
            record['checks']['source_provenance']={'status':'passed','evidence_path':'provenance.json',
                'evidence_sha256':file_sha256(proof)}
            (root/'stage0.json').write_text(json.dumps(record))
            with patch('codex_agent.release.measure', return_value={'runtime_binary_sha256':runtime_sha}):
                self.assertEqual(verify_release(config), manifest)
            with patch('codex_agent.release.measure', return_value={'runtime_binary_sha256':'c'*64}):
                with self.assertRaises(GateError):
                    verify_release(config)
            artifact.write_text('changed evidence')
            with patch('codex_agent.release.measure') as measure:
                with self.assertRaises(GateError):
                    verify_release(config)
                measure.assert_not_called()
