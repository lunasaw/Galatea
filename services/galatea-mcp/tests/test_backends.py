import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from helpers import sha, setup_registry


class BackendTests(unittest.TestCase):
    def test_s3_version_digest_and_size_verified_streaming(self):
        from galatea_mcp.backends.objects import S3Objects
        class Client:
            def get_object(self, **kwargs):
                if kwargs != {'Bucket':'b','Key':'k','VersionId':'v'}:
                    raise AssertionError(kwargs)
                return {'VersionId':'v', 'ContentLength':3, 'Body':io.BytesIO(b'abc')}
        backend = S3Objects(Client())
        ref = {'bucket':'b','key':'k','version_id':'v','sha256':sha(b'abc'),'size_bytes':3}
        self.assertTrue(backend.verify(ref))
        self.assertFalse(backend.verify(ref | {'sha256':'0'*64}))
        self.assertFalse(backend.verify(ref | {'size_bytes':2}))

    def test_ray_fixed_submission_and_cluster_identity(self):
        from galatea_mcp.backends.ray import RayBackend
        from galatea_mcp.contracts import Project
        with tempfile.TemporaryDirectory() as tmp:
            p = Project.model_validate(setup_registry(Path(tmp))['projects'][0])
            jobs = {}
            class Client:
                def submit_job(self, **kwargs):
                    jobs[kwargs['submission_id']] = kwargs
                    return kwargs['submission_id']
                def get_job_info(self, submission_id):
                    return SimpleNamespace(status='RUNNING', metadata=jobs[submission_id]['metadata'])
            client = Client()
            current = {'trainer':'head1','evaluator':'head2'}
            backend = RayBackend({'trainer':client,'evaluator':client}, expected_heads=current.copy(),
                                 probe=lambda:current.copy(), binding=lambda *args:{'signed':'binding'},
                                 runtime_envs={'trainer':{},'evaluator':{}})
            op = {'submission_id':'galatea-py-one','metadata':{'galatea.operation':'op1'},'role':'trial'}
            backend.submit(op, p, p.configs['c1'], p.releases['r1'])
            self.assertEqual(jobs['galatea-py-one']['entrypoint'], 'python scripts/driver.py')
            self.assertEqual(jobs['galatea-py-one']['entrypoint_num_cpus'], 1)
            self.assertEqual(backend.observe(op)['execution'], 'running')
            self.assertNotIn('logs', backend.observe(op))
            current['trainer']='new-head'
            with self.assertRaisesRegex(Exception,'cluster'):
                backend.observe(op)

    def test_shared_serial_endpoint_uses_one_cluster_identity_for_both_roles(self):
        from galatea_mcp.backends.ray import RayBackend
        current = {'trainer': 'head1', 'evaluator': 'head1'}
        backend = RayBackend({}, expected_heads=current.copy(), endpoint_mode='shared_serial_endpoint',
                             probe=lambda: current.copy(), binding=lambda *args: {},
                             runtime_envs={'trainer': {}, 'evaluator': {}})
        self.assertEqual(backend.cluster_id, RayBackend(
            {}, expected_heads=current.copy(), endpoint_mode='shared_serial_endpoint',
            probe=lambda: current.copy(), binding=lambda *args: {},
            runtime_envs={'trainer': {}, 'evaluator': {}}).cluster_id)
        self.assertNotEqual(backend.cluster_id, RayBackend(
            {}, expected_heads=current.copy(), endpoint_mode='separate_endpoints',
            probe=lambda: current.copy(), binding=lambda *args: {},
            runtime_envs={'trainer': {}, 'evaluator': {}}).cluster_id)

    def test_mlflow_reads_proxy_artifacts_and_rejects_unsafe_repository(self):
        from galatea_mcp.backends.mlflow import MLflowEvidence
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)/'download'
            destination.mkdir()
            class Client:
                def get_run(self, run_id):
                    return SimpleNamespace(info=SimpleNamespace(run_id=run_id,experiment_id='1',status='FINISHED',
                        artifact_uri='mlflow-artifacts:/1/r1/artifacts'), data=SimpleNamespace(metrics={'val_rmse':0.2},
                        tags={'galatea.project':'p1','galatea.campaign':'c1','galatea.operation':'op1','galatea.role':'trial'}))
                def list_artifacts(self, run_id, path):
                    return [SimpleNamespace(path='reports/evidence.json',is_dir=False,file_size=3)]
                def download_artifacts(self, run_id, path, dst_path):
                    target=Path(dst_path)/'reports'/'evidence.json'
                    target.parent.mkdir(exist_ok=True)
                    target.write_bytes(b'abc')
                    return str(target)
            backend=MLflowEvidence(Client(), Path(tmp))
            self.assertEqual(backend.read_artifact('r1','reports/evidence.json',3), b'abc')
            self.assertEqual(list(destination.iterdir()), [])
            self.assertTrue(backend.artifact_digest('r1','reports/evidence.json',sha(b'abc'),3))
            with self.assertRaises(Exception):
                backend.read_artifact('r1','reports/evidence.json',2)
            with self.assertRaises(Exception):
                backend.read_artifact('r1','../secret',3)

    def test_execution_binding_signature_and_role_views(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from galatea_mcp.backends.binding import BindingSigner
        from galatea_mcp.contracts import Project
        import base64
        with tempfile.TemporaryDirectory() as tmp:
            p=Project.model_validate(setup_registry(Path(tmp))['projects'][0])
            key=Ed25519PrivateKey.generate()
            signer=BindingSigner(key, tracking_uri='https://tracking.example', role_env={})
            op={'role':'trial','operation_id':'op1','submission_id':'s1','metadata':{},'config_id':'c1',
                'release_id':'r1','campaign_id':'c1','step_id':'base','attempt':1,'readiness_digest':'a'*64,
                'deadline_at':100,'candidate_id':None,'champion_run_id':None}
            env=signer(op,p,p.configs['c1'],p.releases['r1'])
            raw=env['GALATEA_EXECUTION_BINDING'].encode()
            key.public_key().verify(base64.b64decode(env['GALATEA_EXECUTION_SIGNATURE']),raw)
            binding=json.loads(raw)
            self.assertEqual(set(binding['views']), {'train','validation'})
            self.assertNotIn('test',raw.decode())
            self.assertEqual(binding['ray_topology'], 'separate_endpoints')
            self.assertEqual(binding['credential_mode'], 'separate_role_credentials')

    def test_shared_serial_binding_keeps_evaluator_view_isolated(self):
        from galatea_mcp.backends.binding import BindingSigner
        from galatea_mcp.contracts import Project
        import tempfile
        import json
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            p = Project.model_validate(setup_registry(Path(tmp))['projects'][0])
            signer = BindingSigner(
                None,
                tracking_uri='https://tracking.example',
                role_env={},
                ray_addresses={'trainer': 'http://ray', 'evaluator': 'http://ray'},
                ray_topology='shared_serial_endpoint',
                credential_mode='shared_read_only',
            )
            op = {
                'role': 'evaluate', 'operation_id': 'op1', 'submission_id': 's1', 'metadata': {},
                'config_id': 'c1', 'release_id': 'r1', 'campaign_id': 'c1', 'step_id': 'evaluate',
                'attempt': 1, 'readiness_digest': 'a' * 64, 'deadline_at': 100,
                'candidate_id': 'candidate1', 'champion_run_id': 'run1',
                'champion_model_sha256': 'b' * 64,
            }
            binding = json.loads(signer(op, p, p.configs['c1'], p.releases['r1'])['GALATEA_EXECUTION_BINDING'])
            self.assertEqual(binding['ray_topology'], 'shared_serial_endpoint')
            self.assertEqual(binding['credential_mode'], 'shared_read_only')
            self.assertEqual(binding['ray_address'], 'http://ray')
            self.assertEqual(set(binding['views']), {'test'})

    def test_metric_history_is_one_bounded_tracking_api_page(self):
        from galatea_mcp.backends.mlflow import MLflowEvidence
        class HTTP:
            def get(self,path,params):
                self.seen=(path,params)
                return SimpleNamespace(raise_for_status=lambda:None,
                    json=lambda:{'metrics':[{'step':2,'timestamp':3,'value':0.1}], 'next_page_token':'next'})
        http=HTTP()
        with tempfile.TemporaryDirectory() as tmp:
            backend=MLflowEvidence(object(),Path(tmp),history_client=http)
            result=backend.history_page('run1','val_rmse',limit=10,cursor='previous')
        self.assertEqual(result['next_cursor'],'next')
        self.assertEqual(http.seen[1],{'run_id':'run1','metric_key':'val_rmse','max_results':10,'page_token':'previous'})
