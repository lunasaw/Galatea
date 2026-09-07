"""Workload-owned integration checks against the independent MCP wire/data contract."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REPOSITORY=ROOT.parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(REPOSITORY/'services/galatea-mcp/src'))


class MCPContractTests(unittest.TestCase):
    def test_built_release_and_generated_registration_are_directly_inspectable(self):
        from ray_tabular_regression.release import build_release,write_registration_examples
        from galatea_mcp.projects import Registry
        from galatea_mcp.contracts import CampaignSpec
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        key=Ed25519PrivateKey.generate()
        public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
        class Objects:
            def verify(self,ref): return True
            def verify_metadata(self,ref): return True
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp).resolve()
            release=build_release(ROOT,output/'release.zip',public)
            project_path,campaign_path=write_registration_examples(ROOT,output,release,'frozen-env-test')
            project=json.loads(project_path.read_text())
            registry=Registry({'schema_version':'galatea.registry/v1','projects':[project]},Objects())
            spec=CampaignSpec.model_validate(json.loads(campaign_path.read_text()))
            for slot in spec.slots:
                _,config,_,_ = registry.verify(project['project_id'],slot.config_ids[0],slot.release_ids[0],slot.role)
                self.assertEqual(config.seed,42)
            self.assertIn('reports/evidence.json',project['artifact_paths'])

    def test_unsigned_packet_cannot_mint_fitting_admission(self):
        from ray_tabular_regression.admission import issue
        with self.assertRaises((PermissionError,TypeError)):
            issue({'role':'trial'})
