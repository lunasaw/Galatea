"""Opus fallback route for the frozen two-stage blind-reference rubric."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import fcntl
from pathlib import Path

import yaml

from . import topic_blind_reference as core
from . import topic_validation_recovery as recovery
from . import topic_validation_review as review
from ._common import digest, file_digest
from .fact_review_server import _atomic_json
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json
from .topic_context import TopicContractError

METHOD = 'topic-blind-reference-opus-v1'


def load_config(path: Path) -> dict:
    config = yaml.safe_load(path.read_text(encoding='utf-8'))
    expected = {**core.load_config(path.parent / 'topic-blind-reference-v1.yaml'),
                'schema_version': METHOD, 'model': 'claude-opus-4-6'}
    if config != expected:
        raise TopicContractError('invalid frozen opus reference configuration')
    return config


def run_reference(*, scope: str, config_path: Path, output_root: Path, controlled_root: Path,
                  base_url: str, auth_file: Path, authorization_reference: str,
                  packet: Path | None = None, consent: Path | None = None,
                  preflight: Path | None = None, execute: bool = False) -> dict:
    config = load_config(config_path)
    if scope not in {'preflight', 'private'} or not authorization_reference or not base_url.startswith('https://'):
        raise TopicContractError('invalid reference scope or authorization')
    for path in (output_root, packet, consent, preflight):
        if path is not None:
            private_path(path, controlled_root)
    inputs = {str(p): file_digest(p) for p in (config_path,
        config_path.parent / 'topic-blind-reference-v1.yaml',
        config_path.parent.parent / 'scripts/audit_topic_blind_reference_opus.py')}
    sources = {name: file_digest(Path(__file__).with_name(name)) for name in (
        Path(__file__).name, 'topic_blind_reference.py', 'topic_validation_completion.py',
        'topic_validation_budget.py', 'topic_review_budget.py', 'topic_validation_review.py',
        'topic_validation_recovery.py', 'topic_validation_protocol.py', 'topic_adjudication.py',
        'topic_candidates.py', 'fact_review_server.py', '_common.py')}
    if scope == 'preflight':
        cases = core.fixtures()
        config = {**config, 'budget': config['preflight_budget'], 'max_repair_requests': 0}
    else:
        if any(p is None for p in (packet, consent, preflight)):
            raise TopicContractError('private reference requires packet, consent and preflight')
        manifest = verify_manifest(preflight, core.REQUIRED)
        route, route_identity = read_json(preflight / 'report.json'), read_json(preflight / 'identity.json')
        recovery.source_check(route_identity)
        if (manifest['identity'] != route_identity or route_identity['scope'] != 'preflight'
                or route_identity['authorization_reference'] != authorization_reference
                or route_identity['base_url_sha256'] != digest(base_url.rstrip('/'))
                or route_identity['model'] != config['model'] or not route['route_gate_passed']):
            raise TopicContractError('reference preflight not qualified')
        packet_manifest, cases = core.load_cases(packet, consent, config_path, base_url)
        sources.update(packet_manifest['identity']['source_digests'])
        for path in (packet / 'manifest.json', consent, preflight / 'manifest.json'):
            inputs[str(path)] = file_digest(path)
        if len(cases) != config['population'] or file_digest(packet / 'manifest.json') != config['packet_manifest_sha256']:
            raise TopicContractError('reference requires frozen 200-candidate population')
    identity = {'method': METHOD, 'scope': scope, 'model': config['model'], 'inputs': inputs,
        'source_digests': sources, 'budget': config['budget'], 'input_guard': config['input_guard'],
        'authorization_reference': authorization_reference, 'base_url_sha256': digest(base_url.rstrip('/')),
        'prompt_sha256': digest(core.PROMPT), 'schema_sha256': digest(core.SCHEMA),
        'candidate_digests': [r['candidate_sha256'] for r in cases], 'cases_sha256': digest(cases)}
    workspace = output_root / (METHOD + '-' + scope + '_' + digest(identity)[:20])
    plan = {'status': 'planned', 'workspace': str(workspace), 'scope': scope, 'population': len(cases),
            'planned_first_requests': len(cases) * 2, 'budget': config['budget'],
            'max_repair_requests': config['max_repair_requests'], 'model': config['model'], **core.GOVERNANCE}
    recovery.source_check(identity)
    if not execute:
        return plan
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace.chmod(0o700)
    with (workspace / 'run.lock').open('a') as lock:
        (workspace / 'run.lock').chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sealed = (workspace / 'manifest.json').exists()
        if sealed and verify_manifest(workspace, core.REQUIRED)['identity'] != identity:
            raise TopicContractError('reference sealed identity changed')
        if (workspace / 'identity.json').exists() and read_json(workspace / 'identity.json') != identity:
            raise TopicContractError('reference interrupted identity changed')
        if not sealed:
            _atomic_json(workspace / 'identity.json', identity)
            if not (workspace / 'budget.json').exists():
                _atomic_json(workspace / 'budget.json', {'identity_sha256': digest(identity), 'attempts': []})
        journal = core.Journal(workspace, identity, config, base_url, auth_file, sealed)
        if not sealed and read_json(workspace / 'budget.json')['attempts']:
            journal.dispatch_enabled = False
            for case in cases:
                first, _ = journal.request(case, 'selected')
                if first and case['reference']['status'] == 'available':
                    journal.request(case, 'expanded', first['label'])
            journal.dispatch_enabled = True
        def process(case):
            first, error = journal.request(case, 'selected')
            second, second_error = (journal.request(case, 'expanded', first['label'])
                if first and case['reference']['status'] == 'available' else (None, 'first_or_evidence_unavailable'))
            return {'sample_id': case['sample_id'], 'candidate_sha256': case['candidate_sha256'],
                'selected': first, 'expanded': second, 'errors': {'selected': error, 'expanded': second_error},
                'selected_source_ids': case['selected']['source_ids'],
                'expanded_source_ids': case['reference'].get('view', {}).get('source_ids', []), **core.GOVERNANCE}
        with ThreadPoolExecutor(max_workers=1 if sealed or scope == 'preflight' else config['workers']) as pool:
            references = list(pool.map(process, cases))
        ledger = read_json(workspace / 'budget.json')
        if (ledger['identity_sha256'] != digest(identity)
                or {r['request_digest'] for r in ledger['attempts']} != journal.seen
                or len(ledger['attempts']) > config['budget']['max_requests']
                or journal.repair_count > config['max_repair_requests']
                or any(p.stem not in journal.seen for p in workspace.glob('*.json') if len(p.stem) == 64)):
            raise TopicContractError('reference contains unbound requests')
        completed = sum(bool(r['selected'] and r['expanded']) for r in references)
        calibration = [bool(r['selected'] and r['expanded'] and all(
            r[stage]['label']['context'] == case['expected_context'] for stage in ('selected', 'expanded')))
            for case, r in zip(cases, references)] if scope == 'preflight' else []
        report = {**plan, 'status': 'complete' if completed == len(cases) else 'incomplete',
            'completed_references': completed, 'valid_stage_judgments': sum(bool(r[s]) for r in references for s in ('selected', 'expanded')),
            'reference_review_completed': completed == len(cases), 'repair_requests': journal.repair_count,
            'response_failure_counts': dict(Counter(v['error'] for v in journal.results.values() if v['error'])),
            'usage': journal.budget._usage(ledger), 'circuit_reason': journal.halt or ledger.get('halt_reason'),
            'route_gate_passed': scope == 'preflight' and all(calibration) and not journal.halt,
            'synthetic_cases_passed': sum(calibration), 'references_sha256': digest(references),
            'machine_labels_disclosed_to_reference': False, 'same_gateway_error_correlation_possible': True,
            'same_family_as_claude_judge': True, 'p3_accepted': False, 'provider_usage_bound_attested': False}
        recovery.source_check(identity)
        outputs = {'references.json': {'identity': identity, 'references': references}, 'report.json': report}
        if sealed:
            if any(read_json(workspace / n) != value for n, value in outputs.items()):
                raise TopicContractError('reference replay differs from raw reconstruction')
        else:
            for name, value in outputs.items():
                _atomic_json(workspace / name, value)
            names = core.REQUIRED | {p.name for p in workspace.glob('*.json') if len(p.stem) == 64}
            manifest = {'identity': identity, **core.GOVERNANCE,
                        'output_digests': {n: file_digest(workspace / n) for n in sorted(names)}}
            manifest['manifest_sha256'] = digest(manifest)
            _atomic_json(workspace / 'manifest.json', manifest)
        return report
