"""Only approved immutable releases reach the official Ray Jobs API."""
import shlex
from . import __name__ as _package
from ..errors import DomainError
from ..state import digest


class RayBackend:
    def __init__(self, clients, *, expected_heads, probe, binding, runtime_envs):
        self.clients, self.expected_heads, self.probe = clients, expected_heads, probe
        self.binding, self.runtime_envs = binding, runtime_envs
        self.cluster_id = digest({'schema_version': 'galatea.cluster/v1', 'heads': expected_heads})

    def check_cluster(self):
        if self.probe() != self.expected_heads:
            raise DomainError('cluster-identity-changed', next_action='operator-reconcile')

    @staticmethod
    def target(op):
        return 'evaluator' if op['role'] == 'evaluate' else 'trainer'

    def submit(self, operation, project, config, release):
        from pathlib import Path
        self.check_cluster()
        role = self.target(operation)
        runtime = dict(self.runtime_envs[role])
        if set(runtime) - {'env_vars', 'pip', 'conda', 'config'}:
            raise DomainError('unsafe-runtime-env')
        env = dict(runtime.get('env_vars', {}))
        env.update(self.binding(operation, project, config, release))
        runtime.update(working_dir=str(Path(project.root) / release.path), env_vars=env)
        resources = config.resources
        return self.clients[role].submit_job(
            submission_id=operation['submission_id'], entrypoint=shlex.join(release.entrypoint),
            runtime_env=runtime, metadata=operation['metadata'],
            entrypoint_num_cpus=resources.cpus * resources.workers,
            entrypoint_num_gpus=resources.gpus * resources.workers,
            entrypoint_memory=resources.memory_bytes * resources.workers)

    def observe(self, operation):
        self.check_cluster()
        info = self.clients[self.target(operation)].get_job_info(operation['submission_id'])
        status = getattr(info.status, 'value', info.status)
        state = {'PENDING': 'queued', 'RUNNING': 'running', 'STOPPED': 'stopped',
                 'SUCCEEDED': 'succeeded', 'FAILED': 'failed'}.get(status, 'unknown')
        # Raw Ray logs can contain held-out labels or secrets. They are deliberately not fetched.
        return {'execution': state, 'metadata': info.metadata or {}, 'cluster_id': self.cluster_id}

    def stop(self, operation):
        self.check_cluster()
        return self.clients[self.target(operation)].stop_job(operation['submission_id'])
