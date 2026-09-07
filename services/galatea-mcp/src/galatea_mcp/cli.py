"""Trusted composition and offline administration; no model-facing config mutation."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
from pydantic import Field
from .auth import Principal
from .contracts import Strict, Ident, Pos, TOOLS
from .errors import DomainError
from .projects import Registry
from .service import Service
from .state import StateStore


class Identity(Strict):
    principal_id: Ident
    project_ids: list[Ident]
    campaign_ids: list[Ident]
    actions: list[str]


class Target(Strict):
    address: str
    expected_head_id: str
    runtime_env: dict = Field(default_factory=dict)
    env_refs: dict[str, str] = Field(default_factory=dict)


class PlatformConfig(Strict):
    trainer: Target
    evaluator: Target
    tracking_uri: str
    s3_endpoint: str
    s3_region: str = 'us-east-1'
    signing_key_path: str
    artifact_download_root: str


class Deployment(Strict):
    schema_version: str
    state_root: str
    registry_path: str
    principal: Identity
    http_port: Pos = 8791
    token_env: str = 'GALATEA_SERVICE_TOKEN'
    platform: PlatformConfig | None = None


def load_json(path: Path):
    if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('unsafe configuration')
    return json.loads(path.read_text(encoding='utf-8'))


def official_backends(config: PlatformConfig):
    if config is None:
        raise ValueError('platform configuration required')
    from ray.job_submission import JobSubmissionClient
    from ray.util.state import list_nodes
    from mlflow import MlflowClient
    import boto3
    from botocore.config import Config
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from .backends.ray import RayBackend
    from .backends.mlflow import MLflowEvidence
    from .backends.objects import S3Objects
    from .backends.binding import BindingSigner

    targets = {'trainer':config.trainer, 'evaluator':config.evaluator}
    if config.trainer.address == config.evaluator.address:
        raise ValueError('evaluator must use an isolated Ray endpoint')
    clients = {name: JobSubmissionClient(target.address) for name, target in targets.items()}
    def probe():
        observed = {}
        for name, target in targets.items():
            nodes = list_nodes(address=target.address, filters=[('is_head_node','=',True),('state','=','ALIVE')],
                               timeout=10, limit=2, raise_on_missing_output=True)
            if len(nodes) != 1:
                raise DomainError('cluster-identity-unavailable')
            observed[name] = nodes[0].node_id
        return observed
    key_path = Path(config.signing_key_path)
    if key_path.is_symlink() or key_path.stat().st_mode & 0o077:
        raise ValueError('signing key must be a protected regular file (0600)')
    key = load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError('Ed25519 signing key required')
    signer = BindingSigner(key, tracking_uri=config.tracking_uri, role_env={},
                           ray_addresses={name:t.address for name,t in targets.items()})
    envs = {}
    for name, target in targets.items():
        envs[name] = dict(target.runtime_env)
        if 'env_vars' in envs[name]:
            raise ValueError('runtime secrets and environment use env_refs only')
        envs[name]['env_vars'] = {name: os.environ[ref] for name, ref in target.env_refs.items()}
    ray = RayBackend(clients, expected_heads={n:t.expected_head_id for n,t in targets.items()},
                     probe=probe, binding=signer, runtime_envs=envs)
    import httpx
    headers = {'Authorization': 'Bearer ' + os.environ['MLFLOW_TRACKING_TOKEN']} if os.environ.get('MLFLOW_TRACKING_TOKEN') else {}
    basic_auth = (os.environ['MLFLOW_TRACKING_USERNAME'], os.environ.get('MLFLOW_TRACKING_PASSWORD', '')) if os.environ.get('MLFLOW_TRACKING_USERNAME') else None
    history_client = httpx.Client(base_url=config.tracking_uri, headers=headers, auth=basic_auth,
                                 trust_env=False, follow_redirects=False, timeout=30)
    mlflow = MLflowEvidence(MlflowClient(tracking_uri=config.tracking_uri), Path(config.artifact_download_root),
                            history_client=history_client)
    objects = S3Objects(boto3.client('s3', endpoint_url=config.s3_endpoint, region_name=config.s3_region,
                                   config=Config(connect_timeout=10, read_timeout=30, retries={'max_attempts':2})))
    return ray, mlflow, objects


def main(argv=None):
    parser = argparse.ArgumentParser(description='Independent Galatea MCP and trusted offline administration')
    parser.add_argument('--config', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('validate')
    sub.add_parser('preflight')
    register = sub.add_parser('register'); register.add_argument('--spec',type=Path,required=True)
    amend = sub.add_parser('amend'); amend.add_argument('--campaign-id',required=True); amend.add_argument('--changes',type=Path,required=True)
    sub.add_parser('reconcile')
    serve = sub.add_parser('serve'); serve.add_argument('--transport',choices=['http','stdio'],default='http')
    args = parser.parse_args(argv)
    deployment = Deployment.model_validate(load_json(args.config))
    if deployment.schema_version != 'galatea.deployment/v1':
        raise ValueError('unsupported deployment schema')
    if not set(deployment.principal.actions) <= set(TOOLS) | {'*'}:
        raise ValueError('unknown principal action')
    registry_raw = load_json(Path(deployment.registry_path))
    registry = Registry(registry_raw, None)
    if args.command == 'validate':
        print(json.dumps({'status':'valid','projects':list(registry.projects),'platform_probed':False}))
        return 0
    store = StateStore(Path(deployment.state_root).absolute())
    try:
        if args.command in {'register','amend'}:
            service = Service(store, registry, None, None)
            if args.command == 'register':
                service.register(load_json(args.spec))
            else:
                service.amend(args.campaign_id, load_json(args.changes))
            return 0
        ray, evidence, objects = official_backends(deployment.platform)
        registry.objects = objects
        service = Service(store, registry, ray, evidence)
        if args.command == 'preflight':
            ray.check_cluster()
            # Read-only API checks; no final-test objects read and no Jobs admitted.
            for project in registry.projects.values():
                experiment = evidence.client.get_experiment(project.experiment_id)
                if not experiment or experiment.lifecycle_stage != 'active':
                    raise ValueError('MLflow experiment unavailable')
            print(json.dumps({'status':'platform-reachable','cluster_identity':ray.cluster_id,
                              'training_started':False,'evaluation_isolation':'requires-live-acceptance'}))
            return 0
        if args.command == 'reconcile':
            service.reconcile_all()
            return 0
        identity = deployment.principal
        principal = Principal(identity.principal_id, frozenset(identity.project_ids),
                              frozenset(identity.campaign_ids), frozenset(identity.actions))
        from .server import create_http_app, serve_stdio
        if args.transport == 'stdio':
            asyncio.run(serve_stdio(service, principal))
        else:
            import uvicorn
            app = create_http_app(service, principal, os.environ[deployment.token_env])
            uvicorn.run(app, host='127.0.0.1', port=deployment.http_port, log_level='warning')
        return 0
    finally:
        store.close()


if __name__ == '__main__':
    raise SystemExit(main())
