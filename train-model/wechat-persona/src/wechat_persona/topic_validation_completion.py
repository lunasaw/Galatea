"""Final bounded recovery of four HTTP failures; preserve all earlier evidence."""
from __future__ import annotations

import base64
from collections import Counter
import fcntl
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import yaml

from . import topic_validation_http_diagnostic as diagnostic
from . import topic_validation_recovery as recovery
from . import topic_validation_review as review
from ._common import digest, file_digest
from .fact_review_server import _atomic_json
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json
from .topic_context import TopicContractError
from .topic_validation_budget import GuardedBudget, request_table

METHOD = 'topic-validation-completion-v1'


def load_config(path: Path) -> dict:
    config = yaml.safe_load(path.read_text(encoding='utf-8'))
    expected = {'schema_version': METHOD, 'parent_manifest_sha256':
        'cda28b87f736b2cb2a6e5a0fa8f189f7de5190b6c69168cf1a3af422c9d119bb',
        'inherited_judgments': 396, 'missing_judgments': 4,
        'budget': {'max_requests': 8, 'max_repair_requests': 4, 'batch_size': 1, 'workers': 1,
                   'max_input_tokens': 120000, 'max_total_output_tokens': 32000,
                   'max_output_tokens': 4000, 'request_timeout_seconds': 240},
        'input_guard': {'multipliers': {'gpt': 3, 'claude': 2}, 'padding_tokens': 512,
            'headroom_tokens': 20000, 'release_unused_reservations': False,
            'stop_on_reservation_exceeded': True}, 'governance': review.GOVERNANCE}
    if config != expected:
        raise TopicContractError('invalid frozen completion configuration')
    return config


def capture_http(payload: dict, *, base_url: str, endpoint: str, auth_file: Path, timeout: int) -> dict:
    """Keep bounded private bytes, including HTTP error bodies; never print them."""
    key = read_json(auth_file)['OPENAI_API_KEY']
    request = Request(urljoin(base_url.rstrip('/') + '/', endpoint),
        data=json.dumps(payload, ensure_ascii=False).encode(), method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    status, error, body, read_failed = None, None, b'', False
    try:
        with urlopen(request, timeout=timeout) as response:
            status, body = getattr(response, 'status', 200), response.read(diagnostic.BODY_LIMIT + 1)
    except HTTPError as exc:
        status, error = exc.code, 'HTTPError'
        try:
            body = exc.read(diagnostic.BODY_LIMIT + 1)
        except OSError:
            read_failed = True
    except OSError as exc:
        error = type(exc).__name__
    truncated = len(body) > diagnostic.BODY_LIMIT
    encoded = base64.b64encode(body[:diagnostic.BODY_LIMIT]).decode('ascii')
    return {'http_status': status, 'error_type': error or ('body_truncated' if truncated else None),
            'raw_body_base64': encoded, 'raw_body_sha256': digest(encoded),
            'body_truncated': truncated, 'body_read_failed': read_failed}


def load_parent(parent: Path, packet: Path, consent: Path, config_path: Path,
                controlled_root: Path, base_url: str, authorization_reference: str):
    # The diagnostic's read-only loader verifies both historical raw-response chains.
    diagnostic.load_request(parent, packet, consent,
        config_path.parent / 'topic-validation-http-diagnostic-v1.yaml', base_url,
        controlled_root, authorization_reference)
    source = read_json(parent / 'identity.json')
    old = review.load_config(config_path.parent / 'topic-validation-review-v1.yaml')
    policy = review.preparation.load_policy(config_path.parent / old['preparation_config'])
    _, candidates, strata = review.load_packet(packet, old, policy, source['protocol'], consent)
    inherited = read_json(parent / 'decisions.json')['decisions']
    return source, candidates, strata, policy, inherited


def run_completion(*, parent: Path, packet: Path, consent: Path, config_path: Path,
                   preflight: Path, output_root: Path, controlled_root: Path, base_url: str,
                   auth_file: Path, authorization_reference: str, execute: bool = False) -> dict:
    for path in (parent, packet, consent, preflight, output_root):
        private_path(path, controlled_root)
    config = load_config(config_path)
    if file_digest(parent / 'manifest.json') != config['parent_manifest_sha256']:
        raise TopicContractError('completion parent changed')
    source, candidates, strata, policy, inherited = load_parent(
        parent, packet, consent, config_path, controlled_root, base_url, authorization_reference)
    review_policy = review.base.load_config(config_path.parent / 'topic-axes-review-v4.yaml')
    route = review.base.require_preflight(preflight, source['protocol'], review_policy)
    if (read_json(preflight / 'identity.json')['authorization_reference'] != authorization_reference
            or route['bound_returned_models'] != source['models']):
        raise TopicContractError('completion fresh route authorization mismatch')
    keys = {(r['judge'], r['sample_id']) for r in inherited}
    missing = {(j, c['sample_id']) for j in source['models'] for c in candidates} - keys
    if len(inherited) != config['inherited_judgments'] or len(missing) != config['missing_judgments']:
        raise TopicContractError('completion missing population changed')
    first = [{'judge': j, 'ids': [sid], 'phase': 'first'} for j, sid in sorted(missing)]
    inputs = {**source['inputs'], **{str(p): file_digest(p) for p in (
        parent / 'manifest.json', preflight / 'manifest.json', config_path,
        config_path.parent.parent / 'scripts/complete_topic_validation.py')}}
    identity = {**source, 'scope': METHOD, 'inputs': inputs,
        'source_digests': {**source['source_digests'], Path(__file__).name: file_digest(Path(__file__)),
            'topic_validation_http_diagnostic.py': file_digest(Path(diagnostic.__file__))},
        'authorization_reference': authorization_reference, 'route': route, 'first_batches': first,
        'budget': config['budget'], 'input_guard': config['input_guard'],
        'inherited_decisions_sha256': digest(inherited), 'parent_manifest_sha256': config['parent_manifest_sha256']}
    table = request_table(identity, candidates)
    reserved = sum(v['reserve'] for v in table.values())
    if reserved > config['budget']['max_input_tokens'] - config['input_guard']['headroom_tokens']:
        raise TopicContractError('completion worst-case budget exceeded')
    workspace = output_root / (METHOD + '_' + digest(identity)[:20])
    plan = {'status': 'planned', 'workspace': str(workspace), 'parent': str(parent),
        'planned_first_requests': len(first), 'worst_case_input_reservation': reserved,
        'budget': config['budget'], 'inherited_judgments': len(inherited), **review.GOVERNANCE}
    recovery.source_check(identity)
    if not execute:
        return plan
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace.chmod(0o700)
    with (workspace / 'run.lock').open('a') as lock:
        (workspace / 'run.lock').chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sealed = (workspace / 'manifest.json').exists()
        if sealed and verify_manifest(workspace, recovery.REQUIRED)['identity'] != identity:
            raise TopicContractError('completion sealed identity changed')
        if (workspace / 'identity.json').exists() and read_json(workspace / 'identity.json') != identity:
            raise TopicContractError('completion interrupted identity changed')
        if not sealed:
            _atomic_json(workspace / 'identity.json', identity)
            if not (workspace / 'budget.json').exists():
                _atomic_json(workspace / 'budget.json', {'identity_sha256': digest(identity), 'attempts': []})
        decisions, errors, audit = recovery.reconstruct(workspace, identity, candidates, inherited, reconcile=not sealed)
        if not sealed:
            budget = GuardedBudget(workspace, identity, table)
            by_id = {c['sample_id']: c for c in candidates}
            state = {'reason': None, 'consecutive_transport_errors': 0}
            # Recover the circuit from every persisted response before dispatching.
            def sync_circuit():
                state.update(reason=budget.halt_reason(), consecutive_transport_errors=0)
                for attempt in read_json(workspace / 'budget.json')['attempts']:
                    path = workspace / (attempt['request_digest'] + '.json')
                    if not path.exists():
                        continue
                    raw = read_json(path)
                    _, error, _ = review.unpack(raw, identity, by_id[raw['batch']['ids'][0]])
                    if error == 'model_identity_mismatch':
                        state['reason'] = error
                    state['consecutive_transport_errors'] = (state['consecutive_transport_errors'] + 1
                        if raw['http_status'] in identity['transport_circuit_statuses'] else 0)
                    if state['consecutive_transport_errors'] >= identity['transport_circuit_consecutive_errors']:
                        state['reason'] = state['reason'] or 'repeated_transport_errors'
                _atomic_json(workspace / 'circuit.json', state)
            def collect(batches):
                sync_circuit()
                for batch in batches:
                    payload, sha, estimate = review.payload_record(identity, batch, by_id[batch['ids'][0]])
                    attempts = read_json(workspace / 'budget.json')['attempts']
                    if state['reason'] or any(a['request_digest'] == sha for a in attempts):
                        continue
                    slot = budget.reserve(sha, estimate)
                    raw = {**capture_http(payload, base_url=base_url, endpoint='v1/responses',
                        auth_file=auth_file, timeout=config['budget']['request_timeout_seconds']),
                        'request_digest': sha, 'batch': batch, 'payload_sha256': digest(payload), 'reservation': slot}
                    _atomic_json(workspace / (sha + '.json'), raw)
                    _, _, usage = review.unpack(raw, identity, by_id[batch['ids'][0]])
                    budget.settle(slot, usage)
                    sync_circuit()
            collect(first)
            decisions, errors, audit = recovery.reconstruct(workspace, identity, candidates, inherited)
            collect([{'judge': j, 'ids': [sid], 'phase': 'repair'} for (j, sid), error in sorted(errors.items())
                     if error in review.REPAIRABLE][:config['budget']['max_repair_requests']])
            decisions, errors, audit = recovery.reconstruct(workspace, identity, candidates, inherited)
        state = read_json(workspace / 'circuit.json')
        summary = review.summarize(candidates, decisions, strata, policy, identity['models'])
        cases = summary.pop('cases')
        if [r for r in decisions if (r['judge'], r['sample_id']) in keys] != inherited:
            raise TopicContractError('completion modified inherited decisions')
        complete = len(decisions) == len(candidates) * 2
        report = {**plan, **summary, **audit, 'status': 'complete' if complete and not state['reason'] else 'incomplete',
            'machine_review_completed': complete, 'new_valid_judgments': len(decisions) - len(inherited),
            'inherited_judgments_unchanged': True, 'unresolved_response_failures': len(errors),
            'response_failure_counts': dict(Counter(errors.values())), 'circuit_reason': state['reason'],
            'decisions_sha256': digest(decisions), 'provider_usage_bound_attested': False,
            'diagnostic_labels_used': False, 'http_error_bodies_retained_privately': True}
        recovery.source_check(identity)
        outputs = {'decisions.json': {'identity': identity, 'decisions': decisions},
                   'cases.json': {'cases': cases}, 'report.json': report}
        if sealed:
            if any(read_json(workspace / name) != value for name, value in outputs.items()):
                raise TopicContractError('completion replay differs from raw reconstruction')
        else:
            for name, value in outputs.items():
                _atomic_json(workspace / name, value)
            names = recovery.REQUIRED | {p.name for p in workspace.glob('*.json') if len(p.stem) == 64}
            manifest = {'schema_version': METHOD + '-manifest', 'identity': identity, **review.GOVERNANCE,
                'output_digests': {n: file_digest(workspace / n) for n in sorted(names)}}
            manifest['manifest_sha256'] = digest(manifest)
            _atomic_json(workspace / 'manifest.json', manifest)
        return report
