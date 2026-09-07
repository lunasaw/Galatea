"""Sign a fixed execution binding; signing keys stay on the MCP service side."""
import base64
from ..state import canonical


class BindingSigner:
    def __init__(self, key, *, tracking_uri, role_env, ray_addresses=None):
        self.key, self.tracking_uri, self.role_env = key, tracking_uri, role_env
        self.ray_addresses = ray_addresses or {}

    def __call__(self, op, project, config, release):
        role = op['role']
        views = ['test'] if role == 'evaluate' else ['train', 'validation']
        packet = {
            'schema_version': 'galatea.execution/v1', 'project_id': project.project_id,
            **{k: op.get(k) for k in ['campaign_id', 'operation_id', 'submission_id', 'step_id', 'attempt',
                                      'role', 'config_id', 'release_id', 'readiness_digest', 'deadline_at',
                                      'candidate_id', 'champion_run_id', 'champion_model_sha256']},
            'metadata': op['metadata'], 'config_path': config.path, 'config_digest': config.sha256,
            'release_digest': release.sha256, 'code_revision': release.code_revision,
            'environment_digest': release.environment_digest,
            'dataset_digest': project.dataset.manifest_digest, 'split_digest': project.dataset.split_digest,
            'preprocessing': project.dataset.preprocessing, 'metric_definition': project.metric_definition,
            'evaluation_protocol': project.evaluation_protocol, 'objective': project.objective.model_dump(),
            'seed': config.seed, 'resources': config.resources.model_dump(),
            'experiment_id': project.experiment_id, 'tracking_uri': self.tracking_uri,
            'views': {v: project.dataset.views[v].model_dump() for v in views},
            'clean_start': role != 'evaluate',
            'ray_address': self.ray_addresses.get('evaluator' if role == 'evaluate' else 'trainer'),
            'model_artifact_path': project.model_artifact_path,
        }
        encoded = canonical(packet)
        return {**self.role_env.get(role, {}), 'GALATEA_EXECUTION_BINDING': encoded.decode(),
                'GALATEA_EXECUTION_SIGNATURE': base64.b64encode(self.key.sign(encoded)).decode()}
