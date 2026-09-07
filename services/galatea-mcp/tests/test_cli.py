import json
import tempfile
import unittest
from pathlib import Path
import helpers


class CLITests(unittest.TestCase):
    def test_offline_validate_and_register_without_platform_imports(self):
        from galatea_mcp.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            registry=root/'registry.json';registry.write_text(json.dumps(helpers.setup_registry(root)))
            deployment={'schema_version':'galatea.deployment/v1','state_root':str(root/'state'),
                        'registry_path':str(registry),'principal':{'principal_id':'runner','project_ids':['p1'],
                            'campaign_ids':['campaign1'],'actions':['*']},
                        'http_port':8791,'token_env':'GALATEA_SERVICE_TOKEN','platform':None}
            config=root/'config.json'; config.write_text(json.dumps(deployment))
            spec=root/'campaign.json';spec.write_text(json.dumps(helpers.campaign()))
            self.assertEqual(main(['--config',str(config),'validate']),0)
            self.assertEqual(main(['--config',str(config),'register','--spec',str(spec)]),0)
            data=json.loads((root/'state'/'campaigns'/'campaign1.json').read_text())
            self.assertEqual(data['stage'],'baseline')
            with self.assertRaisesRegex(Exception,'platform'):
                main(['--config',str(config),'preflight'])
