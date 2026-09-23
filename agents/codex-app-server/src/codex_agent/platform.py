"""Official backends with a state-free, read-only production preflight."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def deployment_registry(config):
    from galatea_mcp.cli import Deployment, load_json
    from galatea_mcp.projects import Registry
    deployment = Deployment.model_validate({'schema_version': 'galatea.deployment/v1', **config.raw['galatea']})
    registry = Registry(load_json(Path(deployment.registry_path)), None)
    if not config.project_ids <= registry.projects.keys():
        raise ValueError('principal references unknown project')
    return deployment, registry


def read_only_backends(platform):
    """Construct only clients needed for metadata reads: no signing key or job credentials."""
    from ray.util.state import list_nodes
    from mlflow import MlflowClient
    import boto3
    from botocore.config import Config
    from galatea_mcp.backends.objects import S3Objects
    from galatea_mcp.state import digest

    if platform is None:
        raise ValueError('platform configuration required')
    expected = {name: target.expected_head_id for name, target in
                (('trainer', platform.trainer), ('evaluator', platform.evaluator))}
    def check_cluster():
        for target in (platform.trainer, platform.evaluator):
            nodes = list_nodes(address=target.address,
                filters=[('is_head_node', '=', True), ('state', '=', 'ALIVE')],
                timeout=10, limit=2, raise_on_missing_output=True)
            if len(nodes) != 1 or nodes[0].node_id != target.expected_head_id:
                raise ValueError('cluster identity mismatch')
    ray = SimpleNamespace(check_cluster=check_cluster,
        cluster_id=digest({'schema_version':'galatea.cluster/v1','topology':platform.ray_topology,'heads':expected}))
    evidence = SimpleNamespace(client=MlflowClient(tracking_uri=platform.tracking_uri))
    objects = S3Objects(boto3.client('s3', endpoint_url=platform.s3_endpoint, region_name=platform.s3_region,
        config=Config(connect_timeout=10, read_timeout=15, retries={'max_attempts':1})))
    return ray, evidence, objects


def probe_artifact_index(client, project_id, experiment_id):
    from urllib.parse import urlsplit
    from galatea_mcp.state import identifier
    identifier(project_id)
    runs = []
    for role in ('baseline', 'trial'):
        runs = client.search_runs([experiment_id],
            filter_string=f"tags.`galatea.project` = '{project_id}' AND tags.`galatea.role` = '{role}'",
            max_results=1, order_by=['attributes.start_time DESC'])
        if runs:
            break
    if not runs:
        return {'status': 'not-established', 'reason': 'no-scoped-training-run'}
    run = runs[0]
    if (str(run.info.experiment_id) != str(experiment_id)
            or run.data.tags.get('galatea.project') != project_id
            or run.data.tags.get('galatea.role') not in {'baseline', 'trial'}):
        raise ValueError('artifact preflight scope mismatch')
    uri = urlsplit(run.info.artifact_uri)
    if uri.scheme != 'mlflow-artifacts' or uri.netloc:
        raise ValueError('MLflow artifact proxy required')
    entries = client.list_artifacts(run.info.run_id, 'reports')
    return {'status': 'verified', 'run_id': run.info.run_id, 'entries': len(entries),
            'artifact_contents_read': False}


def check_backends(config, registry, ray, evidence, objects):
    ray.check_cluster()
    projects = []
    for project_id in sorted(config.project_ids):
        project = registry.projects[project_id]
        experiment = evidence.client.get_experiment(project.experiment_id)
        if not experiment or experiment.lifecycle_stage != 'active':
            raise ValueError('MLflow experiment unavailable')
        for split in ('train', 'validation'):
            if not objects.verify_metadata(project.dataset.views[split].model_dump()):
                raise ValueError('immutable object metadata mismatch')
        projects.append(project_id)
    return {'status': 'platform-reachable', 'cluster_identity': ray.cluster_id, 'projects': projects,
            'object_metadata': 'train-and-validation-verified', 'training_started': False,
            'final_test_accessed': False, 'campaign_state_opened': False,
            'artifact_contents': 'requires-approved-artifact-probe'}


def preflight(config, *, artifact_index=False):
    deployment, registry = deployment_registry(config)
    ray, evidence, objects = read_only_backends(deployment.platform)
    result = check_backends(config, registry, ray, evidence, objects)
    if artifact_index:
        result['artifact_indexes'] = {project_id: probe_artifact_index(evidence.client, project_id,
            registry.projects[project_id].experiment_id) for project_id in sorted(config.project_ids)}
        if any(value['status'] != 'verified' for value in result['artifact_indexes'].values()):
            result['status'] = 'incomplete'
    return result


def build_service(config):
    from galatea_mcp.auth import Principal
    from galatea_mcp.cli import official_backends
    from galatea_mcp.service import Service
    from galatea_mcp.state import StateStore

    deployment, registry = deployment_registry(config)
    ray, evidence, objects = official_backends(deployment.platform)
    registry.objects = objects
    check_backends(config, registry, ray, evidence, objects)
    store = StateStore(Path(deployment.state_root).absolute())
    try:
        service = Service(store, registry, ray, evidence)
        principal = Principal(config.principal_id, config.project_ids, config.campaign_ids, config.actions)
        return service, principal
    except BaseException:
        store.close()
        raise
