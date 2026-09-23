import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT.parents[1]/'services/galatea-mcp/src'))
from codex_agent import platform


class PlatformPreflightTests(unittest.TestCase):
    def test_preflight_never_opens_writer_state_and_uses_only_visible_non_test_objects(self):
        ray = SimpleNamespace(check_cluster=Mock(), cluster_id='cluster')
        evidence = SimpleNamespace(client=SimpleNamespace(get_experiment=Mock(return_value=SimpleNamespace(lifecycle_stage='active'))))
        objects = SimpleNamespace(verify_metadata=Mock(return_value=True))
        views = {name:SimpleNamespace(model_dump=lambda name=name: {'key':name}) for name in ('train','validation','test')}
        registry = SimpleNamespace(projects={'p':SimpleNamespace(experiment_id='e',dataset=SimpleNamespace(views=views)),
                                            'private':SimpleNamespace(experiment_id='secret')})
        with patch.object(platform, 'read_only_backends', return_value=(ray,evidence,objects)), \
             patch.object(platform, 'deployment_registry', return_value=(SimpleNamespace(platform=None),registry)), \
             patch('galatea_mcp.state.StateStore', side_effect=AssertionError('writer lock requested')):
            result = platform.preflight(SimpleNamespace(project_ids={'p'}))
        self.assertEqual(result['status'],'platform-reachable')
        evidence.client.get_experiment.assert_called_once_with('e')
        self.assertEqual([call.args[0]['key'] for call in objects.verify_metadata.call_args_list], ['train','validation'])
        self.assertFalse(result['training_started'])

    def test_object_version_mismatch_fails_preflight(self):
        ray=SimpleNamespace(check_cluster=lambda:None,cluster_id='cluster')
        evidence=SimpleNamespace(client=SimpleNamespace(get_experiment=lambda _:SimpleNamespace(lifecycle_stage='active')))
        objects=SimpleNamespace(verify_metadata=lambda _:False)
        view=SimpleNamespace(model_dump=lambda:{'key':'train'})
        registry=SimpleNamespace(projects={'p':SimpleNamespace(experiment_id='e',dataset=SimpleNamespace(views={'train':view,'validation':view}))})
        with patch.object(platform,'read_only_backends',return_value=(ray,evidence,objects)), \
             patch.object(platform,'deployment_registry',return_value=(SimpleNamespace(platform=None),registry)):
            with self.assertRaisesRegex(ValueError,'object'):
                platform.preflight(SimpleNamespace(project_ids={'p'}))
