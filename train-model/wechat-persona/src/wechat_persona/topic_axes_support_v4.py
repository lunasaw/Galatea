"""Frozen v4 review statistics and decoding, independent of historical decisions.

Version-local copies keep the hash-bound v2/v3 implementations reproducible.
Source-shaped fixtures, transport JSON and private source checks remain shared.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re

from ._common import digest, file_digest
from .topic_axes_review import (GATE, GOVERNANCE, LEGEND, fixture_candidate, load_fixtures,
                                load_private_inputs, strict_json, wire_schema)
from .topic_context import TopicContractError
from .topic_evidence_audit import evidence_view
from .topic_review_protocol_v4 import METHOD, PROPERTIES, SYSTEM, validate_axes

BATCH_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['results'],
                'properties': {'results': {'type': 'array', 'items': {
                    'type': 'object', 'additionalProperties': False, 'required': ['index', *PROPERTIES],
                    'properties': {'index': {'type': 'integer'}, **PROPERTIES}}}}}


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
    fmt = {'name': 'topic_reply_axes_v4', 'strict': True, 'schema': schema}
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
            'true_precision_claimed': False, 'existing_labels_changed': False, 'training_ready': False,
            'shadow_v2_status_counts': {name: dict(Counter(r['shadow_v2_status'] for r in decisions
                                                       if r['judge'] == name)) for name in policy['judges']},
            'confidence_only_keep_count': {name: sum(r['status'] == 'keep' and r['shadow_v2_status'] != 'keep'
                                                     for r in decisions if r['judge'] == name) for name in policy['judges']}}


def protocol_binding(policy: dict, fixture_path: Path, base_url: str) -> dict:
    names = ('topic_axes_review.py', 'topic_review_protocol.py', 'topic_axes_support_v4.py',
             'topic_review_protocol_v4.py', 'topic_confidence_audit.py', 'topic_evidence_audit.py', 'topic_review_budget.py',
             'topic_adjudication.py', 'topic_cross_review.py', 'topic_candidates.py', 'topic_comparison.py',
             'topic_context.py', 'consent.py', 'redact.py', 'canary.py', '_common.py', 'fact_review_server.py')
    return {'method': METHOD, 'policy_sha256': digest(policy), 'fixture_sha256': file_digest(fixture_path),
            'prompt_sha256': digest(SYSTEM + LEGEND), 'schema_sha256': digest(BATCH_SCHEMA),
            'wire_schema_sha256': digest(wire_schema(BATCH_SCHEMA)), 'judges': policy['judges'],
            'endpoint_sha256': digest(base_url),
            'source_sha256': {name: file_digest(Path(__file__).with_name(name)) for name in names}}
