"""Portable, read-only replay for v2 packages that recorded project-relative inputs."""
from __future__ import annotations

from pathlib import Path

from . import topic_repair_recovery_v2 as recovery
from . import topic_repair_review as original
from ._common import file_digest
from .topic_adjudication import verify_manifest
from .topic_candidates import read_json
from .topic_context import TopicContractError


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_recorded_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def source_check(identity: dict) -> None:
    if (any(file_digest(resolve_recorded_path(path)) != sha
            for path, sha in identity['inputs'].items())
            or any(file_digest(Path(__file__).with_name(name)) != sha
                   for name, sha in identity['source_digests'].items())):
        raise TopicContractError('recovery v2 immutable source changed')


def replay_complete(machine: Path, candidates: list, dispositions: list) -> tuple[list, dict, dict]:
    manifest = verify_manifest(machine, original.REQUIRED)
    identity = read_json(machine / 'identity.json')
    if manifest['identity'] != identity or identity['scope'] != recovery.METHOD:
        raise TopicContractError('unexpected repair recovery v2 identity')
    source_check(identity)
    parent = Path(identity['parent'])
    source, inherited, missing, parent_audit = recovery.parent_evidence(
        parent, candidates, dispositions)
    config_path = PROJECT_ROOT / 'configs/topic-repair-recovery-v2.yaml'
    config = recovery.load_config(config_path)
    preflights = [Path(path) for path in identity['recovery_preflights']]
    policy = original.base.load_config(PROJECT_ROOT / 'configs/topic-axes-review-v4.yaml')
    route = recovery.require_route_stability(
        preflights, source, policy, identity['authorization_reference'],
        config['required_consecutive_route_preflights'])
    attempt_counts, ancestor_repairs = recovery.ancestor_attempt_counts(parent, missing)
    expected = recovery.build_identity(
        parent, source, inherited, missing, parent_audit, attempt_counts,
        ancestor_repairs, route, identity['inputs'], config,
        identity['authorization_reference'], preflights)
    if (identity != expected
            or file_digest(parent / 'manifest.json') != config['parent_manifest_sha256']):
        raise TopicContractError('recovery v2 lineage, route, authority or budget changed')
    new, errors, audit = original.reconstruct(
        machine, identity, candidates, recovery.recovery_table(identity, candidates))
    decisions = sorted([*inherited, *new], key=lambda row: (row['judge'], row['sample_id']))
    cumulative = {key: parent_audit['cumulative_usage'][key] + audit['usage'][key]
                  for key in audit['usage']}
    total = identity['original_total_budget']
    if (cumulative['requests'] > total['max_requests']
            or cumulative['charged_input_tokens'] > total['max_input_tokens']
            or cumulative['charged_output_tokens'] > total['max_total_output_tokens']):
        raise TopicContractError('repair recovery v2 exceeded original total budget')
    audit = {**audit, 'cumulative_usage': cumulative,
             'inherited_judgments': len(inherited)}
    summary = original.summarize(candidates, decisions, dispositions)
    cases = summary.pop('cases')
    report = read_json(machine / 'report.json')
    if (read_json(machine / 'decisions.json') != {'identity': identity, 'decisions': decisions}
            or read_json(machine / 'cases.json') != {'cases': cases}
            or any(report.get(key) != value for key, value in {**summary, **audit}.items())):
        raise TopicContractError('recovery v2 replay differs from sealed evidence')
    return decisions, errors, audit
