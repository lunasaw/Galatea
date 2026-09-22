"""Frozen cross-family evidence review and separately bound input equivalence."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import html
import json
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import tiktoken
import yaml

from ._common import digest, file_digest
from .consent import verify_consent
from .fact_review_server import _atomic_json
from .topic_candidates import private_path, read_json, verified_pilot
from .topic_comparison import proportion, verified_review
from .topic_context import TopicContractError
from .topic_evidence_audit import LABEL_PROPERTIES, SCHEMA, SYSTEM, evidence_view, parse_response, payload
from .topic_review_budget import ReviewBudget


METHOD = 'topic-cross-family-audit-v1'
HARD_RISKS = {'privacy', 'third_party', 'control_or_abuse', 'unsafe', 'identity_or_capability'}


def load_config(path: Path) -> dict:
    policy = yaml.safe_load(path.read_text(encoding='utf-8'))
    if (policy.get('schema_version') != 'topic-cross-review-config-v1' or policy.get('method') != METHOD
            or policy.get('governance') != {'training_run': False, 'human_review_completed': False,
                                          'formal_training_eligible': False, 'promotable': False}
            or set(policy['carry_forward_hard_risks']) != HARD_RISKS):
        raise TopicContractError('invalid cross-review governance')
    limits = {'max_candidates': 200, 'max_changed_candidates': 18, 'batch_size': 6, 'workers': 4,
              'max_requests': 44, 'max_retries': 1, 'max_output_tokens': 3000,
              'max_input_tokens': 350000, 'max_total_output_tokens': 132000,
              'max_batch_input_tokens': 16000, 'request_timeout_seconds': 120}
    for key, maximum in limits.items():
        minimum = 0 if key == 'max_retries' else 1
        if type(policy[key]) is not int or not minimum <= policy[key] <= maximum:
            raise TopicContractError('invalid cross-review budget: ' + key)
    if policy['minimum_confidence'] != .9:
        raise TopicContractError('cross-review must preserve evidence confidence protocol')
    judges = policy['judges']
    if policy['probe_judges'] not in (['independent'], ['independent', 'changed_reference']):
        raise TopicContractError('invalid transport probe selection')
    if set(judges) != {'independent', 'changed_reference'} or judges['independent']['family'] == judges['changed_reference']['family']:
        raise TopicContractError('cross-review requires distinct requested model families')
    for judge in judges.values():
        if (judge['transport'] not in {'responses', 'chat_completions'}
                or not judge['model'].startswith(judge['returned_model_prefix'])
                or not judge['returned_model_prefix'].startswith(judge['family'] + '-')):
            raise TopicContractError('invalid judge identity or transport')
    return policy


def input_identity(row: dict) -> str:
    """Only an exact input, label, and atomic-source match supports equivalence."""
    return digest({key: row[key] for key in ('messages', 'context_messages', 'target_messages',
                                            'context_message_ids', 'target_message_ids', 'cutoff',
                                            'session_id', 'day', 'owner_scope', 'split')})


def validate_label(row: dict, decision: dict) -> dict:
    if decision['sample_id'] != row['sample_id'] or decision['candidate_sha256'] != row['candidate_sha256']:
        raise TopicContractError('source evidence decision binding mismatch')
    label = {'index': 0, **{key: decision[key] for key in LABEL_PROPERTIES if key != 'index'}}
    validated = parse_response({'output_text': json.dumps({'results': [label]})},
                               [evidence_view(row, row['context_messages'], 'selected')], .9)[0]
    for key in (*LABEL_PROPERTIES, 'responds_to_ids', 'required_context_ids'):
        if key != 'index' and decision[key] != validated[key]:
            raise TopicContractError('source evidence indices or eligibility changed')
    return decision


def load_inputs(before: Path, after: Path, review: Path, audit: Path, policy: dict) -> tuple[list, list, dict, dict, list]:
    old_manifest, old = verified_pilot(before)
    new_manifest, new = verified_pilot(after)
    _, previous = verified_review(before, review, old)
    manifest = read_json(audit / 'manifest.json')
    if (manifest['manifest_sha256'] != digest({k: v for k, v in manifest.items() if k != 'manifest_sha256'})
            or any(Path(name).name != name or file_digest(audit / name) != sha for name, sha in manifest['output_digests'].items())
            or manifest['identity']['pilot_manifest_sha256'] != file_digest(before / 'manifest.json')
            or manifest['identity']['prior_review_sha256'] != file_digest(review / 'decisions.json')):
        raise TopicContractError('invalid previous evidence audit')
    report = read_json(audit / 'report.json')
    record = read_json(audit / 'decisions.json')
    if report['status'] != 'complete' or record['identity'] != manifest['identity'] or digest(record['decisions']) != report['decisions_sha256']:
        raise TopicContractError('previous evidence audit is incomplete or changed')
    if manifest['identity']['schema_sha256'] != digest(SCHEMA) or manifest['identity']['prompt_sha256'] != digest(SYSTEM):
        raise TopicContractError('evidence protocol changed before cross-review')
    if policy['probe_judges'] == ['independent']:
        reference = policy['judges']['changed_reference']
        cached_reference = [read_json(path) for path in audit.glob('*.json') if len(path.stem) == 64]
        if (not cached_reference or reference['transport'] != 'responses'
                or any(row['model_requested'] != reference['model']
                       or not row['model_returned'].startswith(reference['returned_model_prefix'])
                       or digest(row['decisions']) != row['decisions_sha256'] for row in cached_reference)):
            raise TopicContractError('reference transport lacks previous successful evidence')
    evidence = {row['sample_id']: row for row in record['decisions'] if row['view_kind'] == 'selected'}
    if (len(evidence) != sum(row['view_kind'] == 'selected' for row in record['decisions'])
            or set(evidence) != {row['sample_id'] for row in old}):
        raise TopicContractError('previous evidence population mismatch')
    new_by_target = {tuple(row['target_message_ids']): row for row in new}
    if (len(old) != len(new) or len(old) > policy['max_candidates']
            or {tuple(row['target_message_ids']) for row in old} != set(new_by_target)
            or new_manifest['identity'].get('reference_pilot_manifest_sha256') != file_digest(before / 'manifest.json')
            or old_manifest['identity']['tokenizer'] != new_manifest['identity']['tokenizer']):
        raise TopicContractError('cross-review requires the frozen target population and tokenizer')
    roster = []
    for row in old:
        current = new_by_target[tuple(row['target_message_ids'])]
        if row['target_messages'] != current['target_messages'] or row['messages'][-1] != current['messages'][-1]:
            raise TopicContractError('cross-review target changed')
        validate_label(row, evidence[row['sample_id']])
        p, q = previous[row['sample_id']]['status'], evidence[row['sample_id']]['status']
        roster.append({'before_sample_id': row['sample_id'], 'after_sample_id': current['sample_id'],
                       'before_candidate_sha256': row['candidate_sha256'], 'after_candidate_sha256': current['candidate_sha256'],
                       'before_input_sha256': input_identity(row), 'after_input_sha256': input_identity(current),
                       'changed': input_identity(row) != input_identity(current),
                       'keep_disagreement': (p == 'keep') != (q == 'keep'),
                       'prior_consensus_keep': p == q == 'keep'})
    if sum(row['changed'] for row in roster) > policy['max_changed_candidates']:
        raise TopicContractError('changed-input budget exceeded')
    return old, new, previous, evidence, roster


def wire_payload(batch: list[dict], policy: dict, judge: dict) -> dict:
    value = payload(batch, {**policy, 'model': judge['model']})
    if judge['transport'] == 'responses':
        return value
    messages = [dict(message) for message in value['input']]
    messages[0]['content'] += ('\n响应必须是符合以下 JSON Schema 的完整 JSON 对象，顶层包含 results 数组；'
                              '每条必须包含全部 required 字段。不要 Markdown 或其他文字。\n' + json.dumps(SCHEMA, ensure_ascii=False))
    return {'model': judge['model'], 'store': False, 'max_tokens': policy['max_output_tokens'],
            'messages': messages, 'response_format': {'type': 'json_schema', 'json_schema': {
                'name': 'reply_evidence_audit', 'strict': True, 'schema': SCHEMA}}}


def decode_response(result: dict, judge: dict, batch: list[dict], minimum_confidence: float) -> tuple[list, dict]:
    model = result.get('model')
    if not isinstance(model, str) or not model.startswith(judge['returned_model_prefix']):
        raise TopicContractError('unexpected returned model identity')
    if judge['transport'] == 'chat_completions':
        choices = result.get('choices', [])
        if len(choices) != 1 or choices[0].get('finish_reason') not in {'stop', 'end_turn'}:
            raise TopicContractError('incomplete chat evidence response')
        content = choices[0].get('message', {}).get('content')
        # Some compatible gateways ignore response_format and wrap otherwise valid JSON.
        if isinstance(content, str):
            fenced = re.fullmatch(r'\s*```(?:json)?\s*\n(.*?)\n```\s*', content, flags=re.DOTALL)
            if fenced:
                content = fenced.group(1)
        parsed = parse_response({'output_text': content}, batch, minimum_confidence)
    else:
        if result.get('status', 'completed') != 'completed':
            raise TopicContractError('incomplete responses evidence response')
        parsed = parse_response(result, batch, minimum_confidence)
    usage = result.get('usage', {})
    normalized = {}
    for target, alternative in (('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens')):
        if target in usage or alternative in usage:
            normalized[target] = usage.get(target, usage.get(alternative))
    return [{**row, 'method': METHOD, 'model_requested': judge['model'], 'model_returned': model,
             'requested_family': judge['family']} for row in parsed], normalized


def fixture_view() -> dict:
    return {'sample_id': 'synthetic-protocol-fixture', 'candidate_sha256': '0' * 64, 'kind': 'selected',
            'source_ids': ['synthetic-m0'], 'case': {'past_messages': [
                {'index': 0, 'speaker': 'self', 'kind': 'text', 'gap_before': False, 'text': '明天几点见？'}],
                'reply': '明天九点见。'}}


def validate_cached(record: dict, batch: dict, judge: dict, request_digest: str) -> None:
    decisions = record['decisions']
    if (record['request_digest'] != request_digest or record['decisions_sha256'] != digest(decisions)
            or record['model_requested'] != judge['model']
            or not isinstance(record.get('model_returned'), str)
            or not record['model_returned'].startswith(judge['returned_model_prefix'])
            or len(decisions) != len(batch['views'])):
        raise TopicContractError('cross-review response cache changed')
    labels = [{'index': index, **{key: row[key] for key in LABEL_PROPERTIES if key != 'index'}}
              for index, row in enumerate(decisions)]
    parsed = parse_response({'output_text': json.dumps({'results': labels})}, batch['views'], .9)
    for row, validated in zip(decisions, parsed, strict=True):
        expected = {**validated, 'method': METHOD, 'model_requested': judge['model'],
                    'model_returned': record['model_returned'], 'requested_family': judge['family'],
                    'scope': batch['scope'], 'judge': batch['judge']}
        if row != expected:
            raise TopicContractError('cached evidence binding changed')


def make_batches(old: list[dict], new: list[dict], roster: list[dict], policy: dict) -> list[dict]:
    old_by_id = {row['sample_id']: row for row in old}
    new_by_id = {row['sample_id']: row for row in new}
    groups = [('independent', 'v3', new),
              ('independent', 'v2_changed', [old_by_id[row['before_sample_id']] for row in roster if row['changed']]),
              ('changed_reference', 'v3_changed', [new_by_id[row['after_sample_id']] for row in roster if row['changed']])]
    counter = tiktoken.get_encoding('o200k_base')
    batches = []
    for judge_name, scope, candidates in groups:
        judge = policy['judges'][judge_name]
        ordered = sorted(candidates, key=lambda row: digest({'seed': policy['seed'], 'target_ids': row['target_message_ids']}))
        views = [evidence_view(row, row['context_messages'], 'selected') for row in ordered]
        batch = []
        for view in views:
            trial = [*batch, view]
            estimate = len(counter.encode(json.dumps(wire_payload(trial, policy, judge), ensure_ascii=False))) + 256
            if batch and (len(trial) > policy['batch_size'] or estimate > policy['max_batch_input_tokens']):
                batches.append({'judge': judge_name, 'scope': scope, 'views': batch})
                batch = [view]
            else:
                batch = trial
        if batch:
            batches.append({'judge': judge_name, 'scope': scope, 'views': batch})
    return batches


def resume_records(source: Path, batches: list[dict], policy: dict, identity: dict) -> tuple[dict, dict]:
    """Explicitly adopt valid responses with identical requests, never failed judgments."""
    previous_identity = read_json(source / 'identity.json')
    report = read_json(source / 'report.json')
    decisions = read_json(source / 'decisions.json')
    for key in ('method', 'roster_sha256', 'prompt_sha256', 'schema_sha256', 'endpoint_sha256', 'authorization_reference'):
        if previous_identity[key] != identity[key]:
            raise TopicContractError('resume review protocol or authorization differs')
    if (report['status'] != 'incomplete' or decisions['identity'] != previous_identity
            or digest(decisions['decisions']) != report['decisions_sha256']
            or any(previous_identity['inputs'].get(path) != sha for path, sha in identity['inputs'].items())):
        raise TopicContractError('resume requires bound incomplete evidence')
    imported, adopted = {}, []
    paths = ['identity.json', 'report.json', 'decisions.json', 'budget.json']
    for batch in batches:
        previous_digest = digest({'identity': previous_identity, 'batch': batch})
        path = source / (previous_digest + '.json')
        if not path.exists():
            continue
        record = read_json(path)
        validate_cached(record, batch, policy['judges'][batch['judge']], previous_digest)
        imported[digest(batch)] = record
        paths.append(path.name)
        if batch['scope'] != 'synthetic_probe':
            adopted.extend(record['decisions'])
    if sorted(adopted, key=lambda row: (row['scope'], row['sample_id'])) != decisions['decisions']:
        raise TopicContractError('resume changed completed inputs or lost valid decisions')
    return imported, {str(source / name): file_digest(source / name) for name in paths}


def summarize(old: list[dict], new: list[dict], previous: dict, evidence: dict, roster: list[dict], decisions: list[dict]) -> dict:
    populations = {'v3': {row['sample_id']: row for row in new},
                   'v2_changed': {row['sample_id']: row for row in old
                                  if any(item['changed'] and item['before_sample_id'] == row['sample_id'] for item in roster)},
                   'v3_changed': {row['sample_id']: row for row in new
                                  if any(item['changed'] and item['after_sample_id'] == row['sample_id'] for item in roster)}}
    seen = set()
    for row in decisions:
        key = (row['scope'], row['sample_id'])
        if key in seen or row['scope'] not in populations or row['sample_id'] not in populations[row['scope']]:
            raise TopicContractError('unexpected or duplicate cross-review decision')
        seen.add(key)
        validate_label(populations[row['scope']][row['sample_id']], row)
    independent = {row['sample_id']: row for row in decisions if row['scope'] == 'v3'}
    paired = {row['sample_id']: row for row in decisions if row['scope'] == 'v2_changed'}
    changed_reference = {row['sample_id']: row for row in decisions if row['scope'] == 'v3_changed'}
    new_by_id = {row['sample_id']: row for row in new}
    consensus_population = sum(row['prior_consensus_keep'] for row in roster)
    consensus_support = 0
    disagreement_strata = {'prior_keep_evidence_nonkeep': Counter(), 'prior_nonkeep_evidence_keep': Counter()}
    changed_transitions = Counter()
    exclusion_reasons = Counter()
    bindings, selected_ids, cases = [], [], []
    for item in roster:
        old_id, new_id = item['before_sample_id'], item['after_sample_id']
        cross = independent.get(new_id)
        old_cross = paired.get(old_id) if item['changed'] else cross
        if item['prior_consensus_keep'] and old_cross and old_cross['status'] == 'keep':
            consensus_support += 1
        if item['keep_disagreement']:
            group = 'prior_keep_evidence_nonkeep' if previous[old_id]['status'] == 'keep' else 'prior_nonkeep_evidence_keep'
            disagreement_strata[group][old_cross['status'] if old_cross else 'not_reviewed'] += 1
        if item['changed'] and cross and old_cross:
            changed_transitions[old_cross['status'] + '->' + cross['status']] += 1
        reference = changed_reference.get(new_id) if item['changed'] else evidence[old_id]
        hard_risks = sorted({decision['reason'] for decision in (previous[old_id], evidence[old_id]) if decision['reason'] in HARD_RISKS})
        shared_anchors = sorted(set(cross['responds_to_ids']) & set(reference['responds_to_ids'])) if cross and reference else []
        if not cross or not reference:
            reason = 'missing_review'
        elif hard_risks:
            reason = 'prior_hard_risk_requires_adjudication'
        elif cross['status'] != 'keep' or reference['status'] != 'keep':
            reason = 'cross_family_keep_not_agreed'
        elif not shared_anchors:
            reason = 'reply_anchor_disagreement'
        else:
            reason = 'selected'
        cases.append({'sample_id': new_id, 'changed_input': item['changed'], 'disposition': reason,
                      'prior_hard_risks': hard_risks, 'independent_decision': cross,
                      'reference_decision': reference, 'previous_input_independent_decision': old_cross,
                      'reference_binding': 'fresh_v3_review' if item['changed'] else 'exact_input_and_source_equivalence'})
        if reason != 'selected':
            exclusion_reasons[reason] += 1
            continue
        current = new_by_id[new_id]
        if not item['changed'] and item['before_input_sha256'] != item['after_input_sha256']:
            raise TopicContractError('cannot bind previous evidence to different input')
        required = sorted(set(cross['required_context_ids']) | set(reference['required_context_ids']))
        if not set(required) <= set(current['context_message_ids']):
            raise TopicContractError('cross-family evidence outside selected input')
        selected_ids.append(new_id)
        bindings.append({'schema_version': 'cross-family-reply-evidence-v1', 'sample_id': new_id,
                         'candidate_sha256': current['candidate_sha256'], 'input_sha256': input_identity(current),
                         'responds_to_ids': shared_anchors, 'required_context_ids': required,
                         'reference_binding': 'fresh_v3_review' if item['changed'] else 'exact_input_and_source_equivalence',
                         'reference_sample_id': reference['sample_id'], 'reference_candidate_sha256': reference['candidate_sha256'],
                         'reference_decision_sha256': digest(reference), 'independent_decision_sha256': digest(cross),
                         'source_reply_to_claimed': False, 'target_text_used_for_context_selection': False,
                         'human_review_completed': False, 'formal_training_eligible': False})
    return {'population': len(new), 'independent_reviewed': len(independent), 'paired_previous_reviewed': len(paired),
            'changed_reference_reviewed': len(changed_reference),
            'independent_v3_decision_counts': dict(Counter(row['status'] for row in independent.values())),
            'independent_v3_metrics': {metric: proportion(sum(row[metric] for row in independent.values()), len(new))
                                       for metric in ('reply_link_correct', 'context_complete')} if new else {},
            'previous_117_consensus_support': proportion(consensus_support, consensus_population) if consensus_population else None,
            'previous_disagreement_strata': {key: dict(value) for key, value in disagreement_strata.items()},
            'changed_input_transitions': dict(changed_transitions), 'selected_count': len(selected_ids),
            'selected_sample_ids': selected_ids, 'bindings': bindings, 'cases': cases, 'excluded_counts': dict(exclusion_reasons),
            'quality_claim': 'cross_requested_model_families_machine_evidence_not_human_ground_truth',
            'provider_model_revision_attested': False, 'human_review_completed': False,
            'formal_training_eligible': False, 'quality_gate_passed': False, 'training_ready': False, 'training_run': False}


def run_cross_review(*, before: Path, after: Path, review: Path, audit: Path, consent: Path,
                     config_path: Path, output_root: Path, controlled_root: Path,
                     base_url: str, auth_file: Path, authorization_reference: str, execute: bool = False,
                     resume_from: Path | None = None) -> dict:
    for path in (before, after, review, audit, consent, output_root):
        private_path(path, controlled_root)
    if not authorization_reference or any(output_root == p or p in output_root.parents for p in (before, after, review, audit)):
        raise TopicContractError('cross-review requires separate output and explicit authorization reference')
    policy = load_config(config_path)
    old, new, previous, evidence, roster = load_inputs(before, after, review, audit, policy)
    authorization = verify_consent(consent, required_purposes={'processing', 'persona_style', 'evaluation'}, required_message_types={'text'})
    if any(read_json(path / 'source-audit.json')['consent_file_sha256'] != authorization['consent_file_sha256'] for path in (before, after)):
        raise TopicContractError('cross-review consent mismatch')
    if read_json(audit / 'manifest.json')['identity']['endpoint_sha256'] != digest(base_url):
        raise TopicContractError('reference evidence endpoint mismatch')
    probes = [{'judge': name, 'scope': 'synthetic_probe', 'views': [fixture_view()]} for name in policy['probe_judges']]
    batches = make_batches(old, new, roster, policy)
    all_batches = probes + batches
    counter = tiktoken.get_encoding('o200k_base')
    for batch in all_batches:
        batch['wire_payload'] = wire_payload(batch['views'], policy, policy['judges'][batch['judge']])
        batch['input_estimate'] = len(counter.encode(json.dumps(batch['wire_payload'], ensure_ascii=False))) + 256
    identity = {'method': METHOD, 'policy_sha256': digest(policy), 'roster_sha256': digest(roster),
                'inputs': {str(path): file_digest(path) for path in (before / 'manifest.json', after / 'manifest.json',
                          review / 'decisions.json', audit / 'manifest.json', consent)},
                'prompt_sha256': digest(SYSTEM), 'schema_sha256': digest(SCHEMA),
                'endpoint_sha256': digest(base_url), 'authorization_reference': authorization_reference,
                'implementation_digests': {name: file_digest(Path(__file__).with_name(name)) for name in (
                    'topic_cross_review.py', 'topic_evidence_audit.py', 'topic_review_budget.py',
                    'topic_candidates.py', 'topic_comparison.py', 'topic_context.py')}}
    imported = {}
    execution_budget = dict(policy)
    if resume_from is not None:
        private_path(resume_from, controlled_root)
        imported, resume_inputs = resume_records(resume_from, all_batches, policy, identity)
        identity['inputs'].update(resume_inputs)
        identity['resume_from'] = str(resume_from)
        execution_budget.update(max_requests=10, max_input_tokens=80000, max_total_output_tokens=30000, max_retries=1)
    identity['execution_budget_sha256'] = digest(execution_budget)
    pending = [batch for batch in all_batches if digest(batch) not in imported]
    estimate = sum(batch['input_estimate'] for batch in pending)
    if (len(pending) > execution_budget['max_requests'] or estimate > execution_budget['max_input_tokens']
            or any(batch['input_estimate'] > policy['max_batch_input_tokens'] for batch in pending)
            or len(pending) * policy['max_output_tokens'] > execution_budget['max_total_output_tokens']):
        raise TopicContractError('cross-review plan exceeds budget')
    workspace = output_root / ('topic-cross-review_' + digest(identity)[:20])
    plan = {'status': 'planned', 'workspace': str(workspace), 'candidate_count': len(new),
            'changed_count': sum(row['changed'] for row in roster), 'disagreement_count': sum(row['keep_disagreement'] for row in roster),
            'union_count': sum(row['changed'] or row['keep_disagreement'] for row in roster),
            'data_batches': len(batches), 'synthetic_probes': len(probes), 'planned_requests': len(pending),
            'adopted_valid_batches': len(imported), 'resume_from': str(resume_from) if resume_from else None,
            'estimated_input_tokens': estimate, 'max_requests': execution_budget['max_requests'],
            'max_input_tokens': execution_budget['max_input_tokens'], 'max_output_tokens': execution_budget['max_total_output_tokens'],
            'judges': policy['judges'], 'training_run': False, 'pricing_status': 'provider_price_not_configured'}
    if not execute:
        return plan
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace.chmod(0o700)
    with (workspace / 'run.lock').open('a') as process_lock:
        (workspace / 'run.lock').chmod(0o600)
        try:
            fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise TopicContractError('cross-review already running') from exc
        if (workspace / 'manifest.json').exists():
            manifest = read_json(workspace / 'manifest.json')
            if (manifest['identity'] != identity or manifest['manifest_sha256'] != digest({k: v for k, v in manifest.items() if k != 'manifest_sha256'})
                    or any(Path(name).name != name or file_digest(workspace / name) != sha for name, sha in manifest['output_digests'].items())):
                raise TopicContractError('completed cross-review changed')
            return read_json(workspace / 'report.json')
        _atomic_json(workspace / 'identity.json', identity)
        _atomic_json(workspace / 'selection.json', {'roster': roster, 'rule': 'all_v3_plus_both_judges_for_every_changed_input'})
        budget = ReviewBudget(workspace, identity, execution_budget)
        key = read_json(auth_file).get('OPENAI_API_KEY')
        if not key:
            raise TopicContractError('configured API credential unavailable')
        stopped = threading.Event()

        def call(batch: dict) -> dict:
            judge = policy['judges'][batch['judge']]
            request_digest = digest({'identity': identity, 'batch': batch})
            cache = workspace / (request_digest + '.json')
            if cache.exists():
                record = read_json(cache)
                validate_cached(record, batch, judge, request_digest)
                return record
            if digest(batch) in imported:
                previous = imported[digest(batch)]
                record = {**previous, 'request_digest': request_digest,
                          'adopted_from': {'workspace': str(resume_from), 'request_digest': previous['request_digest'],
                                           'record_sha256': digest(previous)}}
                validate_cached(record, batch, judge, request_digest)
                _atomic_json(cache, record)
                return record
            for attempt in range(execution_budget['max_retries'] + 1):
                if stopped.is_set():
                    raise TopicContractError('cross-review circuit breaker')
                reservation = budget.reserve(request_digest, batch['input_estimate'])
                endpoint = 'v1/responses' if judge['transport'] == 'responses' else 'v1/chat/completions'
                request = Request(urljoin(base_url.rstrip('/') + '/', endpoint),
                                  data=json.dumps(batch['wire_payload'], ensure_ascii=False).encode(),
                                  headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}, method='POST')
                try:
                    with urlopen(request, timeout=policy['request_timeout_seconds']) as response:
                        result = json.loads(response.read())
                    raw_usage = result.get('usage', {})
                    usage = {target: raw_usage.get(target, raw_usage.get(other))
                             for target, other in (('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens'))
                             if target in raw_usage or other in raw_usage}
                    budget.settle(reservation, usage)
                    _atomic_json(workspace / ('response-' + request_digest + '-' + str(attempt) + '.json'), {
                        'request_digest': request_digest, 'response_sha256': digest(result),
                        'result': result, 'received_at_unix': time.time()})
                    if batch['scope'] == 'synthetic_probe':
                        _atomic_json(workspace / 'synthetic-probe-response.json', {
                            'request_digest': request_digest, 'wire_sha256': digest(batch['wire_payload']),
                            'result': result, 'received_at_unix': time.time()})
                    parsed, _ = decode_response(result, judge, batch['views'], policy['minimum_confidence'])
                    decisions = [{**row, 'scope': batch['scope'], 'judge': batch['judge']} for row in parsed]
                    if batch['scope'] == 'synthetic_probe' and decisions[0]['status'] != 'keep':
                        raise TopicContractError('judge failed synthetic protocol fixture')
                    record = {'request_digest': request_digest, 'decisions': decisions, 'decisions_sha256': digest(decisions),
                              'response_sha256': digest(result), 'model_requested': judge['model'],
                              'model_returned': result.get('model'), 'usage': usage,
                              'received_at_unix': time.time()}
                    _atomic_json(cache, record)
                    return record
                except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
                    if isinstance(exc, HTTPError) and exc.code in {400, 401, 403, 404}:
                        stopped.set()
                    if stopped.is_set() or attempt == execution_budget['max_retries']:
                        code = '_' + str(exc.code) if isinstance(exc, HTTPError) else ''
                        detail = ':' + str(exc) if isinstance(exc, TopicContractError) else ''
                        raise TopicContractError('cross_review_request_failed_' + type(exc).__name__ + code + detail) from exc
                    time.sleep(2)
            raise TopicContractError('cross-review request incomplete')

        # Probe new transports before real chat; unchanged reference transport has verified prior evidence.
        probe_records = [call(probe) for probe in probes]
        completed, failures = [], []
        with ThreadPoolExecutor(max_workers=policy['workers']) as pool:
            futures = {pool.submit(call, batch): index for index, batch in enumerate(batches)}
            for future in as_completed(futures):
                try:
                    completed.extend(future.result()['decisions'])
                except TopicContractError as exc:
                    failures.append({'batch': futures[future], 'reason': str(exc)})
        decisions = sorted(completed, key=lambda row: (row['scope'], row['sample_id']))
        summary = summarize(old, new, previous, evidence, roster, decisions)
        bindings = summary.pop('bindings')
        cases = summary.pop('cases')
        report = {**plan, **summary, 'status': 'complete' if len(decisions) == sum(len(b['views']) for b in batches) and not failures else 'incomplete',
                  'failures': failures, 'decisions_sha256': digest(decisions), 'usage_cumulative': budget.usage(),
                  'probe_models_returned': [record['model_returned'] for record in probe_records]}
        _atomic_json(workspace / 'decisions.json', {'identity': identity, 'decisions': decisions})
        _atomic_json(workspace / 'report.json', report)
        if report['status'] == 'complete':
            if any(file_digest(Path(path)) != sha for path, sha in identity['inputs'].items()):
                raise TopicContractError('cross-review inputs changed during execution')
            publish_package(workspace, new, roster, decisions, bindings, cases, report)
            manifest = {'schema_version': 'topic-cross-review-manifest-v1', 'identity': identity,
                        'human_review_completed': False, 'formal_training_eligible': False, 'training_run': False,
                        'output_digests': {name: file_digest(workspace / name) for name in (
                            'identity.json', 'selection.json', 'decisions.json', 'report.json',
                            'train.draft.jsonl', 'reply-evidence.jsonl', 'adjudication.jsonl', 'review.html')}}
            manifest['manifest_sha256'] = digest(manifest)
            _atomic_json(workspace / 'manifest.json', manifest)
        return report


def publish_package(workspace: Path, candidates: list[dict], roster: list[dict], decisions: list[dict], bindings: list[dict], cases: list[dict], report: dict) -> None:
    selected_ids = set(report['selected_sample_ids'])
    for name, records in (('train.draft.jsonl', [row for row in candidates if row['sample_id'] in selected_ids]),
                          ('reply-evidence.jsonl', bindings), ('adjudication.jsonl', cases)):
        content = ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n' for row in records)
        path = workspace / name
        if path.exists() and path.read_text(encoding='utf-8') != content:
            raise TopicContractError('existing cross-review draft differs')
        if not path.exists():
            with path.open('x', encoding='utf-8') as handle:
                handle.write(content)
            path.chmod(0o600)
    cross = {row['sample_id']: row for row in decisions if row['scope'] == 'v3'}
    flags = {row['after_sample_id']: row for row in roster}
    assessments = {row['sample_id']: row for row in cases}
    labels = {'keep': '建议保留', 'reject': '排除', 'uncertain': '待核验'}
    parts = ['<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">',
             '<title>第三版回复交叉核验</title><style>body{max-width:1040px;margin:40px auto;padding:0 24px;font:16px/1.6 sans-serif}article{border:1px solid #ccc;padding:24px;margin:20px 0}pre{white-space:pre-wrap;word-break:break-word}blockquote{border-left:3px solid #387;padding-left:16px;margin-left:0}</style>',
             '<h1>第三版回复交叉核验</h1><p>不同模型家族的机器证据，未完成人工审核。所有原始候选均保留。</p>']
    for row in candidates:
        decision = cross[row['sample_id']]
        flag = flags[row['sample_id']]
        assessment = assessments[row['sample_id']]
        reference = assessment['reference_decision']
        source = {m['message_id']: m for m in row['context_messages']}
        parts += ['<article><h2>' + ('共识草案' if row['sample_id'] in selected_ids else '待裁定 / 未选入') + '</h2>',
                  '<p>第三版输入：' + ('有改动' if flag['changed'] else '与第二版相同') +
                  '；Claude 核验：' + labels[decision['status']] + '</p>',
                  '<p>GPT 证据：' + labels[reference['status']] + '；证据绑定：' +
                  ('第三版重新审核' if flag['changed'] else '输入和原子来源完全相同') + '</p>',
                  '<h3>实际输入</h3><pre>' + html.escape(row['messages'][1]['content']) + '</pre>',
                  '<h3>原始回复</h3><pre>' + html.escape(row['messages'][-1]['content']) + '</pre>',
                  '<h3>Claude 判断回复所回应的原话</h3>']
        for mid in decision['responds_to_ids']:
            parts.append('<blockquote>' + html.escape(source[mid]['content']) + '</blockquote>')
        parts.append('<details><summary>来源与核验摘要</summary><pre>' + html.escape(json.dumps({
            'sample_id': row['sample_id'], 'candidate_sha256': row['candidate_sha256'],
            'reason': decision['reason'], 'confidence': decision['confidence'],
            'reference_reason': reference['reason'], 'reference_confidence': reference['confidence'],
            'disposition': assessment['disposition'], 'prior_hard_risks': assessment['prior_hard_risks'],
            'required_context_ids': decision['required_context_ids']}, ensure_ascii=False, indent=2)) + '</pre></details></article>')
    parts.append('</html>')
    path = workspace / 'review.html'
    content = '\n'.join(parts)
    if path.exists() and path.read_text(encoding='utf-8') != content:
        raise TopicContractError('existing cross-review page differs')
    if not path.exists():
        with path.open('x', encoding='utf-8') as handle:
            handle.write(content)
        path.chmod(0o600)
