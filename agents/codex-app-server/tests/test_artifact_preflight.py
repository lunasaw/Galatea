from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT.parents[1]/'services/galatea-mcp/src'))
from codex_agent.platform import probe_artifact_index


class ArtifactPreflightTests(unittest.TestCase):
    def test_only_scoped_training_run_artifact_index_is_read(self):
        run=SimpleNamespace(info=SimpleNamespace(run_id='r',experiment_id='e',artifact_uri='mlflow-artifacts:/1/r/artifacts'),
            data=SimpleNamespace(tags={'galatea.project':'p','galatea.role':'baseline'}))
        client=SimpleNamespace(search_runs=Mock(return_value=[run]),list_artifacts=Mock(return_value=[]))
        result=probe_artifact_index(client,'p','e')
        self.assertEqual(result['status'],'verified')
        client.list_artifacts.assert_called_once_with('r','reports')
        self.assertIn("tags.`galatea.role` = 'baseline'",client.search_runs.call_args.kwargs['filter_string'])

    def test_unscoped_or_final_evaluation_run_is_never_opened(self):
        for role in ('evaluate','champion'):
            run=SimpleNamespace(info=SimpleNamespace(run_id='r',experiment_id='e',artifact_uri='mlflow-artifacts:/1/r/artifacts'),
                data=SimpleNamespace(tags={'galatea.project':'p','galatea.role':role}))
            client=SimpleNamespace(search_runs=Mock(return_value=[run]),list_artifacts=Mock())
            with self.assertRaises(ValueError):
                probe_artifact_index(client,'p','e')
            client.list_artifacts.assert_not_called()
