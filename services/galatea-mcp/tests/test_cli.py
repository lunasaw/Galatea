import json
import tempfile
import unittest
from pathlib import Path
import helpers


class CLITests(unittest.TestCase):
    def test_shared_read_only_mode_allows_one_credential_set_for_both_roles(self):
        from galatea_mcp.cli import PlatformConfig

        base = {
            'trainer': {'address': 'http://127.0.0.1:8265', 'expected_head_id': 'head',
                        'env_refs': {'AWS_ACCESS_KEY_ID': 'TRAINER_KEY'}},
            'evaluator': {'address': 'http://127.0.0.1:8265', 'expected_head_id': 'head',
                          'env_refs': {'AWS_ACCESS_KEY_ID': 'TRAINER_KEY'}},
            'ray_topology': 'shared_serial_endpoint',
            'credential_mode': 'shared_read_only',
            'tracking_uri': 'http://127.0.0.1:5000',
            's3_endpoint': 'http://127.0.0.1:9000',
            'artifact_download_root': '/tmp/galatea-downloads',
        }
        config = PlatformConfig.model_validate(base)
        self.assertEqual(config.ray_topology, 'shared_serial_endpoint')
        self.assertEqual(config.credential_mode, 'shared_read_only')

        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            PlatformConfig.model_validate(base | {
                'evaluator': base['evaluator'] | {'expected_head_id': 'other-head'}
            })
        with self.assertRaises(ValidationError):
            PlatformConfig.model_validate(base | {
                'evaluator': base['evaluator'] | {
                    'env_refs': {'AWS_ACCESS_KEY_ID': 'EVALUATOR_KEY'}
                }
            })

    def test_shared_serial_endpoint_can_still_use_separate_role_credentials(self):
        from galatea_mcp.cli import PlatformConfig
        base = {
            'trainer': {'address': 'http://127.0.0.1:8265', 'expected_head_id': 'head',
                        'env_refs': {'AWS_ACCESS_KEY_ID': 'TRAINER_KEY'}},
            'evaluator': {'address': 'http://127.0.0.1:8265', 'expected_head_id': 'head',
                          'env_refs': {'AWS_ACCESS_KEY_ID': 'EVALUATOR_KEY'}},
            'ray_topology': 'shared_serial_endpoint',
            'credential_mode': 'separate_role_credentials',
            'tracking_uri': 'http://127.0.0.1:5000',
            's3_endpoint': 'http://127.0.0.1:9000',
            'artifact_download_root': '/tmp/galatea-downloads',
        }
        self.assertEqual(
            PlatformConfig.model_validate(base).credential_mode,
            'separate_role_credentials',
        )

    def test_shared_read_only_mode_rejects_empty_credential_references(self):
        from galatea_mcp.cli import PlatformConfig
        from pydantic import ValidationError

        base = {
            'trainer': {'address': 'http://127.0.0.1:8265', 'expected_head_id': 'head',
                        'env_refs': {}},
            'evaluator': {'address': 'http://127.0.0.1:8265', 'expected_head_id': 'head',
                          'env_refs': {}},
            'ray_topology': 'shared_serial_endpoint',
            'credential_mode': 'shared_read_only',
            'tracking_uri': 'http://127.0.0.1:5000',
            's3_endpoint': 'http://127.0.0.1:9000',
            'artifact_download_root': '/tmp/galatea-downloads',
        }
        with self.assertRaises(ValidationError):
            PlatformConfig.model_validate(base)

    def test_separate_endpoint_mode_still_rejects_same_address(self):
        from galatea_mcp.cli import PlatformConfig
        from pydantic import ValidationError
        base = {
            'trainer': {'address': 'http://ray', 'expected_head_id': 'head', 'env_refs': {}},
            'evaluator': {'address': 'http://ray', 'expected_head_id': 'head', 'env_refs': {}},
            'tracking_uri': 'http://mlflow', 's3_endpoint': 'http://minio',
            'artifact_download_root': '/tmp/galatea-downloads',
        }
        with self.assertRaises(ValidationError):
            PlatformConfig.model_validate(base)

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
