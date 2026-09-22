"""One immutable HTTP diagnostic; never add its output to review judgments."""
from __future__ import annotations

import base64
import fcntl
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import yaml

from . import topic_validation_recovery as recovery
from . import topic_validation_review as previous
from ._common import digest, file_digest
from .fact_review_server import _atomic_json
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json
from .topic_context import TopicContractError
from .topic_review_budget import ReviewBudget


METHOD = 'topic-validation-http-diagnostic-v1'
BODY_LIMIT = 1024 * 1024
SAFE_CODES = {'invalid_request_error', 'bad_request', 'unsupported_parameter', 'unsupported_value',
    'content_filter', 'content_policy_violation', 'moderation_blocked', 'model_not_found', 'upstream_error',
    'context_length_exceeded', 'invalid_json_schema', 'permission_denied', 'invalid_api_key',
    'rate_limit_exceeded', 'insufficient_quota'}
SAFE_PARAMETERS = {'model', 'input', 'messages', 'temperature', 'max_output_tokens',
                   'text', 'text.format', 'response_format', 'stream'}


def load_config(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding='utf-8'))
    expected = {'schema_version': 'topic-validation-http-diagnostic-config-v1',
        'recovery_config': 'topic-validation-recovery-v1.yaml',
        'failed_manifest_sha256': 'cda28b87f736b2cb2a6e5a0fa8f189f7de5190b6c69168cf1a3af422c9d119bb',
        'missing_judge': 'gpt', 'missing_judgments': 4, 'selection': 'first_missing_sample_id',
        'budget': {'max_requests': 1, 'max_input_tokens': 20000, 'max_total_output_tokens': 4000,
                   'max_output_tokens': 4000, 'request_timeout_seconds': 120}, 'governance': previous.GOVERNANCE}
    if value != expected:
        raise TopicContractError('invalid frozen validation HTTP diagnostic config')
    return value


def load_request(failed: Path, packet: Path, consent: Path, config_path: Path, base_url: str,
                 controlled_root: Path, authorization_reference: str) -> tuple[dict, dict]:
    config = load_config(config_path)
    if file_digest(failed / 'manifest.json') != config['failed_manifest_sha256']:
        raise TopicContractError('diagnostic source changed')
    manifest = verify_manifest(failed, recovery.REQUIRED)
    source = read_json(failed / 'identity.json')
    if (manifest['identity'] != source or source['scope'] != recovery.METHOD
            or not authorization_reference or authorization_reference == source['authorization_reference']):
        raise TopicContractError('diagnostic requires new scope and sealed recovery')
    recovery.source_check(source)
    recovery_config = recovery.load_config(config_path.parent / config['recovery_config'])
    old_config = previous.load_config(config_path.parent / recovery_config['parent_config'])
    policy = previous.preparation.load_policy(config_path.parent / old_config['preparation_config'])
    review_policy = previous.base.load_config(config_path.parent / old_config['review_config'])
    fixture_path, _ = previous.base.core.load_fixtures(config_path.parent / old_config['review_config'], review_policy)
    binding = previous.base.binding_for(review_policy, fixture_path, base_url)
    if source['protocol'] != binding:
        raise TopicContractError('diagnostic route changed')
    _, candidates, strata = previous.load_packet(packet, old_config, policy, binding, consent)
    parent = Path(read_json(failed / 'report.json')['parent'])
    private_path(parent, controlled_root)
    _, inherited, _ = recovery.verify_parent(parent, candidates, strata, policy, recovery_config)
    decisions, errors, _ = recovery.reconstruct(failed, source, candidates, inherited)
    if (read_json(failed / 'decisions.json') != {'identity': source, 'decisions': decisions}
            or len(errors) != config['missing_judgments']
            or any(j != config['missing_judge'] or reason != 'HTTPError' for (j, _), reason in errors.items())):
        raise TopicContractError('diagnostic missing HTTP population changed')
    key = sorted(errors)[0]
    records = [read_json(failed / (a['request_digest'] + '.json')) for a in read_json(failed / 'budget.json')['attempts']]
    selected = [r for r in records if (r['batch']['judge'], r['batch']['ids'][0]) == key]
    if len(selected) != 2 or any(r['http_status'] != 400 for r in selected):
        raise TopicContractError('diagnostic requires two HTTP 400 failures')
    candidate = next(r for r in candidates if r['sample_id'] == key[1])
    payload, _, estimate = previous.payload_record(source, selected[0]['batch'], candidate)
    if (any(r['payload_sha256'] != digest(payload) for r in selected)
            or estimate * 3 + 512 > config['budget']['max_input_tokens']):
        raise TopicContractError('diagnostic request changed or exceeds reservation')
    inputs = dict(source['inputs'])
    for path in (failed / 'manifest.json', config_path, consent,
                 config_path.parent.parent / 'scripts/diagnose_topic_validation_http.py'):
        inputs[str(path)] = file_digest(path)
    identity = {'scope': METHOD, 'protocol': binding, 'inputs': inputs,
        'source_digests': {**source['source_digests'], Path(__file__).name: file_digest(Path(__file__))},
        'payload_sha256': digest(payload), 'candidate_sha256': candidate['candidate_sha256'],
        'source_request_digests': [r['request_digest'] for r in selected], 'budget': config['budget'],
        'authorization_reference': authorization_reference}
    return identity, payload


def response_summary(raw: dict) -> tuple[dict, dict]:
    body = base64.b64decode(raw['body_base64'], validate=True)
    if digest(raw['body_base64']) != raw['body_sha256']:
        raise TopicContractError('diagnostic response changed')
    try:
        parsed = previous.base.core.strict_json(body)
    except ValueError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    provider_usage = parsed.get('usage')
    provider_usage = provider_usage if isinstance(provider_usage, dict) else {}
    usage = {k: v for k, v in provider_usage.items()
             if k in ('input_tokens', 'output_tokens') and type(v) is int and v >= 0}
    error = parsed.get('error')
    error = error if isinstance(error, dict) else {}
    message = str(error.get('message', '')).lower()
    categories = [name for name, needles in {
        'unsupported_parameter': ('unsupported parameter', 'unsupported_parameter', 'unsupported value'),
        'schema_or_format': ('json_schema', 'response_format', 'text.format', 'invalid schema'),
        'context_length': ('context length', 'context_length', 'too many tokens'),
        'content_policy': ('content policy', 'content_policy', 'content_filter', 'moderation', 'safety policy'),
        'authentication': ('api key', 'authentication'),
        'permission': ('permission', 'not permitted'),
        'rate_limit': ('rate limit', 'rate_limit', 'too many requests'),
        'model_routing': ('model not found', 'no available channel', 'model unavailable'),
    }.items() if any(n in message for n in needles)]
    safe = lambda value, allowed: value if isinstance(value, str) and value in allowed else None
    return {'http_status': raw['http_status'], 'transport_error': raw['transport_error'],
        'body_truncated': raw['body_truncated'], 'body_read_failed': raw['body_read_failed'],
        'error_code': safe(error.get('code'), SAFE_CODES), 'error_type': safe(error.get('type'), SAFE_CODES),
        'error_parameter': safe(error.get('param'), SAFE_PARAMETERS), 'message_category_hints': categories,
        'error_message_logged': False, 'judgments_added': 0, 'labels_used': False}, usage


def run_diagnostic(*, failed: Path, packet: Path, consent: Path, config_path: Path,
                   output_root: Path, controlled_root: Path, base_url: str, auth_file: Path,
                   authorization_reference: str, execute: bool = False) -> dict:
    for path in (failed, packet, consent, output_root):
        private_path(path, controlled_root)
    identity, payload = load_request(failed, packet, consent, config_path, base_url, controlled_root, authorization_reference)
    directory = output_root / (METHOD + '_' + digest(identity)[:20])
    for source in (failed, packet):
        if directory.resolve() == source.resolve() or source.resolve() in directory.resolve().parents:
            raise TopicContractError('diagnostic output overlaps source')
    plan = {'status': 'planned', 'workspace': str(directory), 'planned_requests': 1,
            'private_samples_sent': 1 if execute else 0, 'budget': identity['budget'], **previous.GOVERNANCE}
    if not execute:
        return plan
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    with (directory / 'run.lock').open('a') as lock:
        (directory / 'run.lock').chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sealed = (directory / 'manifest.json').exists()
        if sealed and verify_manifest(directory, {'identity.json', 'response.json', 'budget.json', 'report.json'})['identity'] != identity:
            raise TopicContractError('diagnostic replay identity changed')
        if (directory / 'identity.json').exists() and read_json(directory / 'identity.json') != identity:
            raise TopicContractError('diagnostic interrupted identity changed')
        if not sealed:
            _atomic_json(directory / 'identity.json', identity)
        budget = ReviewBudget(directory, identity, identity['budget'])
        sha = digest({'identity': identity, 'payload': payload})
        if not (directory / 'response.json').exists():
            if (directory / 'budget.json').exists() and read_json(directory / 'budget.json')['attempts']:
                raise TopicContractError('diagnostic interrupted request is not redispatched')
            slot = budget.reserve(sha, identity['budget']['max_input_tokens'])
            key = read_json(auth_file)['OPENAI_API_KEY']
            request = Request(urljoin(base_url.rstrip('/') + '/', 'v1/responses'),
                data=json.dumps(payload, ensure_ascii=False).encode(), method='POST',
                headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
            status, transport, body, read_failed = None, None, b'', False
            try:
                with urlopen(request, timeout=identity['budget']['request_timeout_seconds']) as response:
                    status, body = response.status, response.read(BODY_LIMIT + 1)
            except HTTPError as exc:
                status = exc.code
                try:
                    body = exc.read(BODY_LIMIT + 1)
                except OSError:
                    read_failed = True
            except OSError as exc:
                transport = type(exc).__name__
            encoded = base64.b64encode(body[:BODY_LIMIT]).decode('ascii')
            raw = {'request_digest': sha, 'reservation': slot, 'http_status': status, 'transport_error': transport,
                'body_base64': encoded, 'body_sha256': digest(encoded), 'body_truncated': len(body) > BODY_LIMIT,
                'body_read_failed': read_failed}
            _atomic_json(directory / 'response.json', raw)
        raw = read_json(directory / 'response.json')
        ledger = read_json(directory / 'budget.json')
        if (ledger['identity_sha256'] != digest(identity) or len(ledger['attempts']) != 1
                or raw['request_digest'] != sha or raw['reservation'] != 0
                or ledger['attempts'][0]['request_digest'] != sha
                or ledger['attempts'][0]['input_reserve'] != identity['budget']['max_input_tokens']
                or ledger['attempts'][0]['output_reserve'] != identity['budget']['max_output_tokens']):
            raise TopicContractError('diagnostic reservation changed')
        summary, usage = response_summary(raw)
        if sealed:
            if (any(ledger['attempts'][0].get(k) != v for k, v in usage.items())
                    or any(k in ledger['attempts'][0] and k not in usage for k in ('input_tokens', 'output_tokens'))):
                raise TopicContractError('diagnostic settled usage changed')
        else:
            budget.settle(0, usage)
            ledger = read_json(directory / 'budget.json')
        report = {**plan, **summary, 'status': 'complete', 'usage': ReviewBudget._usage(ledger)}
        recovery.source_check(identity)
        if sealed:
            if read_json(directory / 'report.json') != report:
                raise TopicContractError('diagnostic report differs from raw response')
            return report
        _atomic_json(directory / 'report.json', report)
        manifest = {'schema_version': METHOD + '-manifest', 'identity': identity, **previous.GOVERNANCE,
                    'output_digests': {n: file_digest(directory / n) for n in ('identity.json', 'response.json', 'budget.json', 'report.json')}}
        manifest['manifest_sha256'] = digest(manifest)
        _atomic_json(directory / 'manifest.json', manifest)
        return report
