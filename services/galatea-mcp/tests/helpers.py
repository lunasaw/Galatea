"""Synthetic API boundary fixtures; never execute a model or connect to a platform."""
import copy
import hashlib
import json
import zipfile
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def setup_registry(root):
    (root / 'releases').mkdir()
    config = {'seed': 42, 'hyperparameters': {'alpha': 1.0}}
    raw = json.dumps(config).encode()
    (root / 'config.json').write_bytes(raw)
    with zipfile.ZipFile(root / 'releases' / 'r1.zip', 'w') as archive:
        archive.writestr('scripts/driver.py', '# fixed driver fixture; never executed\n')
        archive.writestr('config.json', raw)
    release_sha = sha((root / 'releases' / 'r1.zip').read_bytes())
    views = {name: {'bucket': 'dataset', 'key': name + '.json', 'version_id': 'v1',
                    'sha256': sha(name.encode()), 'size_bytes': len(name)}
             for name in ['train', 'validation', 'test']}
    dataset = {'dataset_id': 'd1', 'manifest_digest': 'a' * 64, 'split_digest': 'b' * 64,
               'preprocessing': 'identity-v1', 'holdout_identity': sha(b'test'),
               'holdout_untouched': True, 'views': views}
    project = {'project_id': 'p1', 'root': str(root), 'task': 'regression',
               'objective': {'metric': 'val_rmse', 'direction': 'min'},
               'metric_definition': 'rmse-v1', 'evaluation_protocol': 'isolated-v1',
               'experiment_id': '1', 'dataset': dataset,
               'releases': {'r1': {'path': 'releases/r1.zip', 'sha256': release_sha,
                                  'code_revision': 'abc123', 'environment_digest': 'c' * 64,
                                  'entrypoint': ['python', 'scripts/driver.py'],
                                  'deadline_enforced': True}},
               'configs': {'c1': {'path': 'config.json', 'sha256': sha(raw), 'seed': 42,
                                  'resources': {'cpus': 1, 'gpus': 0, 'memory_bytes': 1024,
                                                'workers': 1, 'seconds': 10, 'cleanup_seconds': 2}}},
               'evaluation_isolated': True,
               'quality_gates': [{'metric': 'test_rmse', 'direction': 'min', 'threshold': 0.5}],
               'artifact_paths': ['model/model.json', 'reports/evidence.json']}
    return {'schema_version': 'galatea.registry/v1', 'projects': [project]}


def campaign(cid='campaign1'):
    return {'campaign_id': cid, 'project_id': 'p1', 'request_revision': 1,
            'expires_at': 10000, 'approved_by': 'operator',
            'slots': [{'step_id': name, 'role': role, 'config_ids': ['c1'],
                       'release_ids': ['r1'], 'max_attempts': 2 if role != 'evaluate' else 1}
                      for name, role in [('base', 'baseline'), ('trial1', 'trial'),
                                         ('champion', 'champion'), ('evaluate', 'evaluate')]],
            'budget': {'cpu_seconds': 60, 'gpu_seconds': 0, 'max_trials': 1}}


class FakeRay:
    cluster_id = 'cluster1'
    def __init__(self):
        self.jobs = {}
        self.lose_response = False
        self.unavailable = False
    def submit(self, operation, project, config, release):
        self.jobs[operation['submission_id']] = {'execution': 'queued',
            'metadata': operation['metadata'], 'cluster_id': self.cluster_id, 'logs': ''}
        if self.lose_response:
            raise TimeoutError('private backend secret must not escape')
    def observe(self, operation):
        if self.unavailable:
            raise TimeoutError()
        return copy.deepcopy(self.jobs.get(operation['submission_id']))
    def stop(self, operation):
        self.jobs[operation['submission_id']]['execution'] = 'stopped'


class FakeEvidence:
    def __init__(self):
        self.runs = {}
        self.artifacts = {}
    def for_operation(self, project, campaign, operation):
        return [copy.deepcopy(r) for r in self.runs.values()
                if r.get('operation_id') == operation['operation_id']]
    def get_run(self, run_id):
        return copy.deepcopy(self.runs[run_id])
    def read_artifact(self, run_id, path, max_bytes):
        raw = self.artifacts[(run_id, path)]
        if len(raw) > max_bytes:
            raise ValueError('oversize')
        return raw
    def artifact_digest(self, run_id, path, expected, max_bytes):
        raw = self.artifacts[(run_id, path)]
        return len(raw) <= max_bytes and sha(raw) == expected
    def history(self, run_id, metric):
        return [{'step': 1, 'timestamp': 1, 'value': self.runs[run_id]['metrics'][metric]}]


class FakeObjects:
    def verify_metadata(self, ref):
        return True
    def verify(self, ref):
        return True


def finish(service, ray, evidence, cid, operation_id, metric=0.2, intact=True):
    c = service.store.read('campaigns', cid)
    op = c['operations'][operation_id]
    p = service.registry.get(c['spec']['project_id'])
    rid = 'run-' + operation_id[3:]
    ray.jobs[op['submission_id']]['execution'] = 'succeeded'
    model = b'{"weights":[1],"intercept":0}'
    lineage = {'project_id': p.project_id, 'campaign_id': cid, 'operation_id': operation_id,
               'submission_id': op['submission_id'], 'config_id': op['config_id'],
               'config_digest': p.configs[op['config_id']].sha256,
               'release_id': op['release_id'], 'release_digest': p.releases[op['release_id']].sha256,
               'dataset_digest': p.dataset.manifest_digest, 'split_digest': p.dataset.split_digest,
               'preprocessing': p.dataset.preprocessing, 'metric_definition': p.metric_definition,
               'evaluation_protocol': p.evaluation_protocol, 'seed': 42, 'role': op['role'],
               'readiness_digest': op['readiness_digest'], 'clean_start': True,
               'candidate_id': op.get('candidate_id'), 'champion_run_id': op.get('champion_run_id')}
    report = {'schema_version': 'galatea.evidence/v1', 'lineage': lineage,
              'artifacts': [{'path': 'model/model.json', 'sha256': sha(model), 'size_bytes': len(model)}],
              'integrity': {'roundtrip': True, 'load_verified': True},
              'metrics': {('test_rmse' if op['role'] == 'evaluate' else 'val_rmse'): metric},
              'final_test_status': 'passed' if op['role'] == 'evaluate' else 'not-run'}
    if op['role'] == 'evaluate':
        champion_op = next(o for o in c['operations'].values() if o['run_id'] == c['champion_run_id'])
        report['lineage']['model_sha256'] = sha(model)
    evidence.runs[rid] = {'run_id': rid, 'operation_id': operation_id, 'project_id': p.project_id,
                          'campaign_id': cid, 'experiment_id': p.experiment_id, 'role': op['role'],
                          'status': 'FINISHED', 'metrics': report['metrics']}
    evidence.artifacts[(rid, 'reports/evidence.json')] = json.dumps(report).encode()
    evidence.artifacts[(rid, 'model/model.json')] = model if intact else b'corrupt'
    service.reconcile_all()
    return rid


import unittest
import tempfile

class ServiceFixture(unittest.TestCase):
    def setUp(self):
        from galatea_mcp.state import StateStore
        from galatea_mcp.projects import Registry
        from galatea_mcp.service import Service
        from galatea_mcp.auth import Principal
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.registry_data = setup_registry(root)
        self.registry = Registry(self.registry_data, FakeObjects())
        self.store = StateStore(root / 'state')
        self.addCleanup(self.store.close)
        self.ray, self.evidence = FakeRay(), FakeEvidence()
        self.service = Service(self.store, self.registry, self.ray, self.evidence, clock=lambda: 100)
        self.principal = Principal('user', frozenset({'p1'}), frozenset({'campaign1'}), frozenset({'*'}))
        self.service.register(campaign())

    def call(self, name, **kwargs):
        return self.service.call(self.principal, 'galatea_' + name,
                                 {'project_id': 'p1', 'campaign_id': 'campaign1', **kwargs})
    def plan(self, **kwargs):
        return self.call('plan_run', step_id='base', attempt=1, release_id='r1',
                         config_id='c1', role='baseline', **kwargs)['plan_id']
    def submit(self, plan, key='k1'):
        return self.call('submit_job', plan_id=plan, idempotency_key=key)

