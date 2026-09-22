"""Bounded v2 preprocessing review, gated by a frozen synthetic protocol check."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import base64
import fcntl
import json
from pathlib import Path
import re
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import tiktoken
import yaml

from ._common import digest, file_digest
from .consent import verify_consent
from .fact_review_server import _atomic_json
from .topic_adjudication import verified_cross, verify_manifest
from .topic_candidates import private_path, read_json, write_jsonl
from .topic_context import TopicContractError
from .topic_evidence_audit import evidence_view
from .topic_review_budget import ReviewBudget
from .topic_review_protocol import METHOD, PROPERTIES, SYSTEM, validate_axes


GOVERNANCE = {'training_run': False, 'human_review_completed': False,
              'formal_training_eligible': False, 'promotable': False}
GATE = {'population': 24, 'min_status_agreements': 22, 'min_core_agreements': 22, 'max_false_keeps': 0}
LEGEND = '''每条独立核验。self 是对方，target 是拟学习的人；reply 是 target 的原始回复。
past_messages 数组每行为 [原消息索引, speaker, 消息类型, 前面是否省略, 原文]。
results 中 index 是输入 cases 的序号；responds_to_indices 和 required_context_indices 是本条原消息索引。
返回完整 JSON 对象，不输出 Markdown；每条必须包含全部 required 字段。JSON Schema：
'''
BATCH_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['results'],
                'properties': {'results': {'type': 'array', 'items': {
                    'type': 'object', 'additionalProperties': False, 'required': ['index', *PROPERTIES],
                    'properties': {'index': {'type': 'integer'}, **PROPERTIES}}}}}


def strict_json(content):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise TopicContractError('duplicate response field')
            result[key] = value
        return result

    def nonfinite(_):
        raise TopicContractError('nonfinite response number')

    return json.loads(content, object_pairs_hook=pairs, parse_constant=nonfinite)


def load_config(path: Path) -> dict:
    policy = yaml.safe_load(path.read_text(encoding='utf-8'))
    if (policy['schema_version'] != 'topic-axes-review-config-v2' or policy['method'] != METHOD
            or policy['governance'] != GOVERNANCE or policy['batch_size'] != 6 or policy['workers'] != 2
            or {k: policy['calibration'][k] for k in GATE} != GATE
            or {k: policy['private_review'][k] for k in ('population', 'previous_selected', 'previous_pending', 'previous_hard_risks')}
               != {'population': 200, 'previous_selected': 58, 'previous_pending': 142, 'previous_hard_risks': 12}
            or policy['private_review']['max_repair_requests'] != 4
            or set(policy['judges']) != {'gpt', 'claude'}):
        raise TopicContractError('invalid frozen axes policy')
    for name, transport in (('gpt', 'responses'), ('claude', 'chat_completions')):
        judge = policy['judges'][name]
        if (judge['family'] != name or judge['transport'] != transport
                or not judge['model'].startswith(judge['returned_model_prefix'])
                or not judge['returned_model_prefix'].startswith(name + '-')):
            raise TopicContractError('invalid axes judge identity')
    for scope, ceilings in (('calibration', (8, 40000, 24000)), ('private_review', (72, 500000, 216000))):
        limits = dict(zip(('max_requests', 'max_input_tokens', 'max_total_output_tokens'), ceilings))
        limits.update(max_output_tokens=3000, request_timeout_seconds=120)
        for key, limit in limits.items():
            if type(policy[scope][key]) is not int or not 1 <= policy[scope][key] <= limit:
                raise TopicContractError('invalid axes request budget')
    return policy


def load_fixtures(config_path: Path, policy: dict) -> tuple[Path, list[dict]]:
    path = config_path.parent / policy['synthetic_fixtures']
    if path.is_symlink() or config_path.parent.resolve() not in path.resolve().parents:
        raise TopicContractError('fixtures outside config directory')
    fixtures = json.loads(path.read_text(encoding='utf-8'))
    if len(fixtures) != 24 or len({f['id'] for f in fixtures}) != 24:
        raise TopicContractError('axes calibration requires 24 unique fresh cases')
    for fixture in fixtures:
        expected = fixture['expected']
        if (not fixture['past'] or expected['status'] not in {'keep', 'reject', 'uncertain'}
                or any(type(expected[k]) is not bool for k in ('reply_link_correct', 'context_complete'))
                or not expected['allowed_anchor_sets'] or not fixture['id'].startswith('axes-fresh-')
                or any(m[0] not in {'self', 'target'} or not m[1] for m in fixture['past'])):
            raise TopicContractError('invalid synthetic axes reference')
        for anchors in expected['allowed_anchor_sets']:
            if (bool(anchors) != expected['reply_link_correct'] or len(set(anchors)) != len(anchors)
                    or any(type(i) is not int or not 0 <= i < len(fixture['past'])
                           or fixture['past'][i][0] != 'self' for i in anchors)):
                raise TopicContractError('invalid synthetic reference anchors')
    return path, fixtures


def fixture_candidate(fixture: dict) -> dict:
    """Synthetic source-shaped records; no dataset, execution or training identity."""
    scope = {'owner_scope': 'synthetic', 'session_id': fixture['id'], 'day': '2000-01-01', 'split': 'train'}
    messages = [{**scope, 'message_id': fixture['id'] + '-' + str(i), 'role': role, 'content': content,
                 'kind': 'text', 'order': i, 'timestamp': f'2000-01-01T00:00:{i:02d}+00:00'}
                for i, (role, content) in enumerate([*fixture['past'], ['target', fixture['reply']]])]
    row = {**scope, 'schema_version': 'topic-reply-candidate-v1', 'synthetic_fixture': True,
           'sample_id': fixture['id'], 'context_messages': messages[:-1], 'target_messages': messages[-1:],
           'context_message_ids': [m['message_id'] for m in messages[:-1]],
           'target_message_ids': [messages[-1]['message_id']],
           'cutoff': {'timestamp': messages[-1]['timestamp'], 'source_record_index': messages[-1]['order']},
           'messages': [{'role': 'assistant', 'content': fixture['reply']}]}
    return {**row, 'candidate_sha256': digest(row)}


def wire_schema(value):
    # Compatible gateways may not accept these keywords; the local schema still enforces them.
    if isinstance(value, dict):
        return {k: wire_schema(v) for k, v in value.items() if k not in {'minimum', 'maximum', 'uniqueItems'}}
    if isinstance(value, list):
        return [wire_schema(v) for v in value]
    return value


def wire_payload(candidates: list[dict], judge: dict, budget: dict) -> dict:
    cases = []
    for i, row in enumerate(candidates):
        view = evidence_view(row, row['context_messages'], 'selected')['case']
        cases.append({'index': i, 'reply': view['reply'], 'past_messages': [
            [m['index'], m['speaker'], m['kind'], int(m['gap_before']), m['text']] for m in view['past_messages']]})
    schema = wire_schema(BATCH_SCHEMA)
    messages = [{'role': 'system', 'content': SYSTEM + LEGEND + json.dumps(schema, ensure_ascii=False)},
                {'role': 'user', 'content': json.dumps({'cases': cases}, ensure_ascii=False)}]
    common = {'model': judge['model'], 'store': False}
    fmt = {'name': 'topic_reply_axes_v2', 'strict': True, 'schema': schema}
    if judge['transport'] == 'responses':
        return {**common, 'max_output_tokens': budget['max_output_tokens'], 'input': messages,
                'text': {'format': {'type': 'json_schema', **fmt}}}
    return {**common, 'max_tokens': budget['max_output_tokens'], 'messages': messages,
            'response_format': {'type': 'json_schema', 'json_schema': fmt}}


def decode_response(response: dict, judge: dict, candidates: list[dict]) -> tuple[list, dict]:
    if not isinstance(response, dict):
        raise TopicContractError('invalid provider response')
    model = response.get('model')
    if not isinstance(model, str) or not model.startswith(judge['returned_model_prefix']):
        raise TopicContractError('unexpected axes model identity')
    if judge['transport'] == 'responses':
        if response.get('status') != 'completed':
            raise TopicContractError('incomplete axes response')
        content = response.get('output_text') or ''.join(part.get('text', '') for item in response.get('output', [])
                  for part in item.get('content', []) if part.get('type') == 'output_text')
    else:
        choices = response.get('choices', [])
        if len(choices) != 1 or choices[0].get('finish_reason') not in {'stop', 'end_turn'}:
            raise TopicContractError('incomplete axes response')
        content = choices[0].get('message', {}).get('content')
    if isinstance(content, str):
        fenced = re.fullmatch(r'\s*```(?:json)?\s*\n(.*?)\n```\s*', content, flags=re.DOTALL)
        if fenced:
            content = fenced.group(1)
    try:
        result = strict_json(content)
    except (TypeError, ValueError) as exc:
        raise TopicContractError('invalid axes JSON') from exc
    if not isinstance(result, dict) or set(result) != {'results'} or not isinstance(result['results'], list):
        raise TopicContractError('invalid axes envelope')
    indexed = {}
    for label in result['results']:
        if (not isinstance(label, dict) or type(label.get('index')) is not int
                or not 0 <= label['index'] < len(candidates)):
            raise TopicContractError('unknown axes case index')
        indexed.setdefault(label['index'], []).append(label)
    decisions, failures = [], {}
    for i, candidate in enumerate(candidates):
        if len(indexed.get(i, [])) != 1:
            failures[candidate['sample_id']] = 'missing_or_duplicate_case'
            continue
        try:
            decision = validate_axes(candidate, {k: v for k, v in indexed[i][0].items() if k != 'index'})
        except TopicContractError:
            failures[candidate['sample_id']] = 'invalid_case_axes'
            continue
        decisions.append({**decision, 'model_requested': judge['model'], 'model_returned': model,
                          'requested_family': judge['family']})
    return decisions, failures


def validate_decisions(candidates: list[dict], decisions: list[dict], policy: dict) -> dict:
    by_id = {r['sample_id']: r for r in candidates}
    indexed = {}
    for decision in decisions:
        key = (decision['judge'], decision['sample_id'])
        if key in indexed or key[0] not in policy['judges'] or key[1] not in by_id:
            raise TopicContractError('unexpected axes decision population')
        expected = validate_axes(by_id[key[1]], decision['axes'])
        judge = policy['judges'][key[0]]
        if (any(decision.get(k) != v for k, v in expected.items())
                or decision['model_requested'] != judge['model'] or decision['requested_family'] != judge['family']
                or not decision['model_returned'].startswith(judge['returned_model_prefix'])):
            raise TopicContractError('axes decision binding mismatch')
        indexed[key] = decision
    return indexed


def calibration_summary(fixtures: list[dict], decisions: list[dict], policy: dict) -> dict:
    indexed = validate_decisions([fixture_candidate(f) for f in fixtures], decisions, policy)
    details, counts = [], {}
    for name in policy['judges']:
        group = []
        for fixture in fixtures:
            row = indexed.get((name, fixture['id']))
            expected = fixture['expected']
            different = [k for k in ('status', 'reply_link_correct', 'context_complete')
                         if row is None or row[k] != expected[k]]
            group.append({'fixture_id': fixture['id'], 'judge': name, 'rule': fixture['rule'],
                          'reviewed': row is not None, 'differing_axes': different,
                          'status_agrees': 'status' not in different, 'core_agrees': not different,
                          'false_keep': bool(row and row['status'] == 'keep' and expected['status'] != 'keep'),
                          'anchors_agree': bool(row and any(set(row['axes']['responds_to_indices']) == set(a)
                                                           for a in expected['allowed_anchor_sets'])),
                          'expected_status': expected['status'], 'observed_status': row['status'] if row else 'missing'})
        counts[name] = {'population': len(fixtures), 'reviewed': sum(r['reviewed'] for r in group),
                        **{k: sum(r[k] for r in group) for k in ('status_agrees', 'core_agrees', 'false_keep', 'anchors_agree')}}
        details.extend(group)
    passed = all(c['population'] == c['reviewed'] == 24 and c['status_agrees'] >= 22
                 and c['core_agrees'] >= 22 and c['false_keep'] == 0 for c in counts.values())
    return {'protocol_gate_passed': passed, 'gate': GATE, 'judges': counts, 'cases': details,
            'reference_origin': 'developer_authored_fresh_synthetic_not_human_truth',
            'private_samples_reviewed': 0, 'true_precision_claimed': False}


def private_summary(candidates: list[dict], prior: list[dict], decisions: list[dict], policy: dict) -> dict:
    indexed = validate_decisions(candidates, decisions, policy)
    previous = {r['sample_id']: r for r in prior}
    if set(previous) != {r['sample_id'] for r in candidates} or len(previous) != len(prior):
        raise TopicContractError('prior axes population mismatch')
    cases, selected = [], []
    for row in candidates:
        sid = row['sample_id']
        old = previous[sid]
        pair = [indexed.get((name, sid)) for name in policy['judges']]
        shared = sorted(set.intersection(*(set(r['responds_to_ids']) for r in pair))) if all(pair) else []
        if old['prior_hard_risks']:
            reason = 'prior_hard_risk_requires_adjudication'
        elif not all(pair):
            reason = 'missing_review'
        elif any(r['status'] != 'keep' for r in pair):
            reason = 'axes_keep_not_agreed'
        elif not shared:
            reason = 'reply_anchor_disagreement'
        else:
            reason = 'selected'
            selected.append(sid)
        cases.append({'sample_id': sid, 'candidate_sha256': row['candidate_sha256'], 'disposition': reason,
                      'previous_group': 'reference_58' if old['disposition'] == 'selected' else 'pending_142',
                      'prior_hard_risks': old['prior_hard_risks'], 'shared_responds_to_ids': shared,
                      'required_context_ids': sorted({mid for r in pair if r for mid in r['required_context_ids']}),
                      'decision_sha256': {name: digest(indexed[(name, sid)]) for name in policy['judges'] if (name, sid) in indexed},
                      **GOVERNANCE})
    return {'population': len(candidates), 'reviewed': len(decisions), 'selected_count': len(selected),
            'selected_sample_ids': selected, 'cases': cases,
            'decision_counts': {name: dict(Counter(r['status'] for r in decisions if r['judge'] == name)) for name in policy['judges']},
            'disposition_counts': dict(Counter(r['disposition'] for r in cases)),
            'transitions': {name: dict(Counter(r['disposition'] for r in cases if r['previous_group'] == name))
                            for name in ('reference_58', 'pending_142')},
            'true_precision_claimed': False, 'existing_labels_changed': False, 'training_ready': False}


def protocol_binding(policy: dict, fixture_path: Path, base_url: str) -> dict:
    names = ('topic_axes_review.py', 'topic_review_protocol.py', 'topic_evidence_audit.py', 'topic_review_budget.py',
             'topic_adjudication.py', 'topic_cross_review.py', 'topic_candidates.py', 'topic_comparison.py',
             'topic_context.py', 'consent.py', 'redact.py', 'canary.py', '_common.py', 'fact_review_server.py')
    return {'method': METHOD, 'policy_sha256': digest(policy), 'fixture_sha256': file_digest(fixture_path),
            'prompt_sha256': digest(SYSTEM + LEGEND), 'schema_sha256': digest(BATCH_SCHEMA),
            'wire_schema_sha256': digest(wire_schema(BATCH_SCHEMA)), 'judges': policy['judges'],
            'endpoint_sha256': digest(base_url),
            'source_sha256': {name: file_digest(Path(__file__).with_name(name)) for name in names}}


def require_calibration(directory: Path, binding: dict, fixtures: list[dict], policy: dict) -> dict:
    manifest = verify_manifest(directory, {'identity.json', 'decisions.json', 'report.json', 'budget.json', 'cases.json'})
    identity = read_json(directory / 'identity.json')
    record = read_json(directory / 'decisions.json')
    report = read_json(directory / 'report.json')
    if (identity != manifest['identity'] or identity['scope'] != 'calibration' or identity['protocol'] != binding
            or record['identity'] != identity or report['status'] != 'complete'
            or report['decisions_sha256'] != digest(record['decisions'])
            or any(manifest.get(k) != v for k, v in GOVERNANCE.items())):
        raise TopicContractError('calibration protocol binding mismatch')
    summary = calibration_summary(fixtures, record['decisions'], policy)
    cases = summary.pop('cases')
    if (not summary['protocol_gate_passed'] or any(report.get(k) != v for k, v in summary.items())
            or read_json(directory / 'cases.json') != {'cases': cases}):
        raise TopicContractError('synthetic protocol gate failed; private review blocked')
    return {'manifest_sha256': file_digest(directory / 'manifest.json'), 'protocol_gate_passed': True,
            'kind': 'synthetic_protocol_readiness_only'}


def load_private_inputs(paths: dict, policy: dict, controlled_root: Path, base_url: str) -> tuple[list, list, dict]:
    for name in ('before', 'after', 'review', 'audit', 'cross', 'consent'):
        private_path(paths[name], controlled_root)
    _, candidates, prior, _ = verified_cross(paths['before'], paths['after'], paths['review'], paths['audit'],
                                             paths['cross'], paths['cross_policy'], controlled_root)
    expected = policy['private_review']
    if (len(candidates) != expected['population']
            or sum(r['disposition'] == 'selected' for r in prior) != expected['previous_selected']
            or sum(bool(r['prior_hard_risks']) for r in prior) != expected['previous_hard_risks']):
        raise TopicContractError('private axes review requires original 200/58/142/12 population')
    authorization = verify_consent(paths['consent'], required_purposes={'processing', 'persona_style', 'evaluation'},
                                   required_message_types={'text'})
    for name in ('before', 'after'):
        if read_json(paths[name] / 'source-audit.json')['consent_file_sha256'] != authorization['consent_file_sha256']:
            raise TopicContractError('axes consent binding mismatch')
    if read_json(paths['audit'] / 'manifest.json')['identity']['endpoint_sha256'] != digest(base_url):
        raise TopicContractError('private review gateway changed')
    inputs = {str(paths[name] / 'manifest.json'): file_digest(paths[name] / 'manifest.json')
              for name in ('before', 'after', 'audit', 'cross')}
    inputs.update({str(paths['consent']): file_digest(paths['consent']),
                   str(paths['review'] / 'decisions.json'): file_digest(paths['review'] / 'decisions.json'),
                   str(paths['cross_policy']): file_digest(paths['cross_policy'])})
    return candidates, prior, inputs


def run_review(*, scope: str, config_path: Path, output_root: Path, controlled_root: Path,
               base_url: str, auth_file: Path, authorization_reference: str, execute: bool = False,
               calibration: Path | None = None, private_inputs: dict | None = None) -> dict:
    private_path(output_root, controlled_root)
    if scope not in {'calibration', 'private_review'} or not base_url or not authorization_reference:
        raise TopicContractError('axes scope, endpoint and authorization are required')
    policy = load_config(config_path)
    fixture_path, fixtures = load_fixtures(config_path, policy)
    binding = protocol_binding(policy, fixture_path, base_url)
    receipt, inputs, prior = None, {}, []
    if scope == 'private_review':
        if calibration is None or private_inputs is None:
            raise TopicContractError('private axes review requires calibration and source bindings')
        private_path(calibration, controlled_root)
        receipt = require_calibration(calibration, binding, fixtures, policy)
        candidates, prior, inputs = load_private_inputs(private_inputs, policy, controlled_root, base_url)
        for source in [calibration, *(v for k, v in private_inputs.items() if k != 'cross_policy')]:
            if output_root.resolve() == source.resolve() or source.resolve() in output_root.resolve().parents:
                raise TopicContractError('axes output must be separate from source')
    else:
        candidates = [fixture_candidate(f) for f in fixtures]
    budget_policy = policy[scope]
    ordered = sorted(candidates, key=lambda row: digest({'seed': policy['seed'], 'sample_id': row['sample_id']}))
    batches = [{'judge': name, 'ids': [r['sample_id'] for r in ordered[start:start + policy['batch_size']]], 'phase': 'first'}
               for name in policy['judges'] for start in range(0, len(ordered), policy['batch_size'])]
    by_id = {r['sample_id']: r for r in candidates}
    counter = tiktoken.get_encoding('o200k_base')

    def request_data(batch):
        rows = [by_id[sid] for sid in batch['ids']]
        payload = wire_payload(rows, policy['judges'][batch['judge']], budget_policy)
        estimate = len(counter.encode(json.dumps(payload, ensure_ascii=False))) + 256
        return rows, payload, estimate

    estimate = sum(request_data(batch)[2] for batch in batches)
    if (len(batches) > budget_policy['max_requests'] or estimate > budget_policy['max_input_tokens']
            or len(batches) * budget_policy['max_output_tokens'] > budget_policy['max_total_output_tokens']):
        raise TopicContractError('axes plan exceeds frozen budget')
    identity = {'scope': scope, 'protocol': binding, 'inputs': inputs, 'calibration': receipt,
                'population_sha256': digest([r['candidate_sha256'] for r in candidates]),
                'prior_sha256': digest(prior), 'authorization_reference': authorization_reference,
                'budget': budget_policy, 'batch_plan_sha256': digest(batches)}
    workspace = output_root / ('topic-axes-' + scope + '_' + digest(identity)[:20])
    plan = {'status': 'planned', 'workspace': str(workspace), 'scope': scope, 'population': len(candidates),
            'planned_requests': len(batches), 'estimated_input_tokens': estimate, 'budget': budget_policy,
            'calibration': receipt, 'protocol_binding_sha256': digest(binding), **GOVERNANCE}
    if not execute:
        return plan
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace.chmod(0o700)
    with (workspace / 'run.lock').open('a') as lock:
        (workspace / 'run.lock').chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (workspace / 'manifest.json').exists():
            manifest = verify_manifest(workspace, {'identity.json', 'report.json', 'decisions.json', 'cases.json', 'budget.json'})
            if manifest['identity'] != identity:
                raise TopicContractError('axes workspace identity changed')
            return read_json(workspace / 'report.json')
        if (workspace / 'identity.json').exists() and read_json(workspace / 'identity.json') != identity:
            raise TopicContractError('axes workspace identity changed')
        _atomic_json(workspace / 'identity.json', identity)
        budget = ReviewBudget(workspace, identity, budget_policy)
        key = read_json(auth_file)['OPENAI_API_KEY']
        reserved = {r['request_digest'] for r in read_json(workspace / 'budget.json')['attempts']} if (workspace / 'budget.json').exists() else set()

        def call(batch):
            rows, payload, input_estimate = request_data(batch)
            request_digest = digest({'identity': identity, 'batch': batch, 'payload': payload})
            path = workspace / (request_digest + '.json')
            judge = policy['judges'][batch['judge']]
            if path.exists():
                record = read_json(path)
                if (record['request_digest'] != request_digest or record['batch'] != batch
                        or record['payload_sha256'] != digest(payload)
                        or record['raw_body_sha256'] != digest(record['raw_body_base64'])):
                    raise TopicContractError('axes raw response cache changed')
            elif request_digest in reserved:
                return [], {sid: 'interrupted_request_not_redispatched' for sid in batch['ids']}
            else:
                attempt = budget.reserve(request_digest, input_estimate)
                endpoint = 'v1/responses' if judge['transport'] == 'responses' else 'v1/chat/completions'
                request = Request(urljoin(base_url.rstrip('/') + '/', endpoint), data=json.dumps(payload, ensure_ascii=False).encode(),
                                  headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}, method='POST')
                try:
                    with urlopen(request, timeout=budget_policy['request_timeout_seconds']) as response:
                        raw_body = base64.b64encode(response.read()).decode('ascii')
                    error = None
                except OSError as exc:
                    raw_body, error = None, type(exc).__name__
                record = {'request_digest': request_digest, 'batch': batch, 'payload_sha256': digest(payload),
                          'raw_body_base64': raw_body, 'raw_body_sha256': digest(raw_body),
                          'error_type': error, 'reservation': attempt}
                # Persist successful or malformed provider output before any semantic parsing.
                _atomic_json(path, record)
            if record['error_type']:
                return [], {sid: record['error_type'] for sid in batch['ids']}
            try:
                result = strict_json(base64.b64decode(record['raw_body_base64'], validate=True))
                if isinstance(result, dict):
                    raw = result.get('usage', {})
                    usage = {k: raw.get(k, raw.get(alt)) for k, alt in (('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens'))
                             if k in raw or alt in raw}
                    budget.settle(record['reservation'], usage)
                decisions, failures = decode_response(result, judge, rows)
            except (ValueError, TypeError) as exc:
                if not isinstance(exc, TopicContractError):
                    return [], {sid: 'invalid_provider_JSON' for sid in batch['ids']}
                return [], {sid: str(exc) for sid in batch['ids']}
            return [{**r, 'judge': batch['judge'], 'request_digest': request_digest} for r in decisions], failures

        decisions, failed = [], {}
        with ThreadPoolExecutor(max_workers=policy['workers']) as pool:
            for batch, (valid, missing) in zip(batches, pool.map(call, batches), strict=True):
                decisions.extend(valid)
                failed.update({(batch['judge'], sid): reason for sid, reason in missing.items()})
        # Each valid case is final, including reject/uncertain. Only format/missing failures may be retried once.
        if scope == 'private_review' and failed:
            repairs = []
            for name in policy['judges']:
                ids = sorted(sid for judge_name, sid in failed if judge_name == name)
                for start in range(0, len(ids), policy['batch_size']):
                    repairs.append({'judge': name, 'ids': ids[start:start + policy['batch_size']], 'phase': 'repair'})
            for batch in repairs[:budget_policy['max_repair_requests']]:
                try:
                    valid, _ = call(batch)
                except TopicContractError as exc:
                    if str(exc) != 'review_budget_or_circuit_breaker':
                        raise
                    break
                decisions.extend(valid)
                for row in valid:
                    failed.pop((row['judge'], row['sample_id']), None)
        decisions.sort(key=lambda r: (r['judge'], r['sample_id']))
        summary = calibration_summary(fixtures, decisions, policy) if scope == 'calibration' else private_summary(candidates, prior, decisions, policy)
        cases = summary.pop('cases')
        report = {**plan, **summary, 'status': 'complete' if len(decisions) == 2 * len(candidates) else 'incomplete',
                  'usage': budget.usage(), 'unresolved_response_failures': len(failed),
                  'response_failure_counts': dict(Counter(failed.values())), 'decisions_sha256': digest(decisions),
                  'provider_model_revision_attested': False}
        if (protocol_binding(policy, fixture_path, base_url) != binding or load_config(config_path) != policy
                or any(file_digest(Path(path)) != sha for path, sha in inputs.items())):
            raise TopicContractError('axes source changed during execution')
        _atomic_json(workspace / 'decisions.json', {'identity': identity, 'decisions': decisions})
        _atomic_json(workspace / 'cases.json', {'cases': cases})
        _atomic_json(workspace / 'report.json', report)
        if scope == 'private_review':
            selected = set(summary['selected_sample_ids'])
            write_jsonl(workspace / 'train.draft.jsonl', [r for r in candidates if r['sample_id'] in selected])
        names = ['identity.json', 'decisions.json', 'cases.json', 'report.json', 'budget.json']
        names += [p.name for p in workspace.glob('*.json') if len(p.stem) == 64]
        if scope == 'private_review':
            names.append('train.draft.jsonl')
        manifest = {'schema_version': 'topic-axes-review-manifest-v2', 'identity': identity, **GOVERNANCE,
                    'output_digests': {name: file_digest(workspace / name) for name in names}}
        manifest['manifest_sha256'] = digest(manifest)
        _atomic_json(workspace / 'manifest.json', manifest)
        return report
