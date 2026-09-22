"""Freeze a validation-only data review packet without inference or model fitting.

Keep the historical train-only schema and source hashes unchanged. Reuse the
frozen selector, candidate construction, tokenizer, privacy and reference checks.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import shutil
import tempfile
from zoneinfo import ZoneInfo

import jsonschema
import tiktoken
import yaml

from . import topic_axes_review_v4 as axes
from ._common import digest, file_digest
from .canary import DEFAULT_PATTERN
from .datasets import _publish_directory_noreplace
from .redact import scan_adjacent_messages
from .reply_links import validate_references
from .topic_adjudication import verify_manifest
from .topic_candidates import (PATTERNS, PROJECT_ROOT, build_candidate, load_policy as load_candidate_policy,
    private_path, read_json, source_identity, tokenizer_identity, verified_pilot, write_json, write_jsonl)
from .topic_context import AtomicMessage, TopicContractError, merge_turns
from .topic_review import candidate_strata


VERSION = 'topic-validation-preparation-v1'
CANDIDATE_VERSION = 'topic-reply-validation-candidate-v1'
GOVERNANCE = {'training_run': False, 'human_review_completed': False,
              'formal_training_eligible': False, 'promotable': False, 'external_requests': 0}


def load_policy(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding='utf-8'))
    expected = {
        'schema_version': VERSION, 'candidate_config': 'daily-topic-sft-v3.yaml',
        'review_config': 'topic-axes-review-v4.yaml',
        'protocol_document': '../docs/daily-topic-sft-validation-protocol.md',
        'development_manifest_sha256': '544b9fd8586b2fd26049eacd4ee0e65dc113edfa6c0224d220fdab0ce0d97407',
        'completed_review_manifest_sha256': '7c9d13d2ccd0c88cfd3b5d7fedcfa1419676804d116ed1e7c19f5da22a12da0a',
        'allowed_splits': ['validation'],
        'selection': {'seed': 53, 'max_days': 50, 'max_candidates': 200, 'max_target_attempts': 1000,
                      'max_selected_messages': 12000,
                      'method': 'hash_order_structurally_valid_without_label_selection',
                      'backfill_after_review': False},
        'strata': {'minimum_count': 20, 'short_reply_max_characters': 8, 'low_confidence_threshold': .9,
                   'required': ['ordinary', 'short_reply', 'explicit_reference', 'interleaved_proxy', 'low_confidence'],
                   'overlapping': True},
        'quality': {'reply_link_min': .98, 'context_complete_min': .95, 'machine_keep_precision_min': .95,
                    'confidence_interval': 'wilson_95_descriptive',
                    'unknown_reference': 'count_as_unverified_in_denominator',
                    'reference': 'blinded_review_of_all_200_selected_inputs_and_available_past',
                    'machine_agreement_is_precision': False, 'automatic_p3_acceptance': False},
        'prospective_review': {'batch_size': 1, 'workers': 2, 'max_requests': 440, 'max_input_tokens': 1800000,
                               'max_total_output_tokens': 1760000, 'max_output_tokens': 4000,
                               'request_timeout_seconds': 240, 'max_repair_requests': 40},
        'resources': {'cpus': 2, 'memory_gb': 8, 'num_gpus': 0}, 'governance': GOVERNANCE,
    }
    if value != expected:
        raise TopicContractError('invalid frozen validation preparation policy')
    return value


def validation_schema() -> dict:
    """Explicit versioned adapter; do not relax the existing train-only schema."""
    schema = deepcopy(read_json(PROJECT_ROOT / 'schemas/topic-reply-candidate.schema.json'))
    schema['$id'] = 'galatea://wechat-persona/' + CANDIDATE_VERSION
    schema['properties']['schema_version']['const'] = CANDIDATE_VERSION
    schema['properties']['split']['const'] = 'validation'
    schema['$defs']['message']['properties']['split']['const'] = 'validation'
    return schema


def load_validation_days(source: Path, universe: dict, owner: str, policy: dict, timezone_name: str):
    """Scan metadata, then decode only chosen validation rows; never test bodies."""
    metadata = {}
    days = set()
    unassigned_metadata_count = 0
    path = source / 'redacted/messages.jsonl'
    with path.open(encoding='utf-8') as handle:
        for number, line in enumerate(handle):
            matches = PATTERNS['message_id'].findall(line)
            if len(matches) != 1:
                raise TopicContractError('ambiguous source message identity')
            mid = json.loads(matches[0])
            if mid not in universe:
                # The frozen importer also retains normalized records excluded
                # from sessions. They cannot enter any validation population.
                unassigned_metadata_count += 1
                continue
            if universe[mid][1] != 'validation':
                continue
            values = {}
            for field in ('timestamp', 'source_record_index'):
                matches = PATTERNS[field].findall(line)
                if len(matches) != 1:
                    raise TopicContractError('ambiguous validation metadata')
                values[field] = json.loads(matches[0])
            if not values['timestamp'] or type(values['source_record_index']) is not int:
                raise TopicContractError('validation date or order missing')
            parsed = datetime.fromisoformat(values['timestamp'])
            if parsed.tzinfo is None or mid in metadata:
                raise TopicContractError('validation timezone or message identity invalid')
            day = parsed.astimezone(ZoneInfo(timezone_name)).date().isoformat()
            metadata[mid] = (number, day, values)
            days.add(day)
    if {mid for mid, (_, split) in universe.items() if split == 'validation'} != set(metadata):
        raise TopicContractError('validation lineage coverage incomplete')
    selection = policy['selection']
    chosen_days = sorted(sorted(days, key=lambda day: (digest({'day': day, 'seed': selection['seed']}), day))[
                         :selection['max_days']])
    selected = {number: (mid, day, values) for mid, (number, day, values) in metadata.items() if day in chosen_days}
    if len(selected) > selection['max_selected_messages']:
        raise TopicContractError('validation message budget exceeded')
    grouped = defaultdict(list)
    with path.open(encoding='utf-8') as handle:
        for number, line in enumerate(handle):
            if number not in selected:
                continue
            mid, day, values = selected[number]
            row = json.loads(line)
            if row['message_id'] != mid or any(row[key] != value for key, value in values.items()):
                raise TopicContractError('validation source changed between scans')
            sid, split = universe[mid]
            grouped[(sid, day)].append(AtomicMessage(mid, sid, owner, split,
                datetime.fromisoformat(row['timestamp']).astimezone(timezone.utc).isoformat(),
                row['source_record_index'], day, row['speaker_role'], row['text_redacted'] or '',
                row['message_kind'], row.get('reply_to')))
    for messages in grouped.values():
        messages.sort(key=lambda row: (row.timestamp, row.order))
    return grouped, chosen_days, {'validation_days_available': len(days), 'selected_days': len(chosen_days),
                                'selected_validation_messages': len(selected), 'session_day_slices': len(grouped),
                                'unassigned_source_records_skipped_without_body_decode': unassigned_metadata_count}


def strata_for(candidate: dict) -> list[str]:
    proxies = candidate_strata(candidate) - {'ordinary'}
    if 'context_topic_return' in proxies:
        proxies.add('interleaved_proxy')
    if not proxies.intersection({'short_reply', 'explicit_reference', 'interleaved_proxy',
                                 'multiple_context_categories', 'unresolved_category'}):
        proxies.add('ordinary')
    return sorted(proxies)


def validation_view(candidate: dict, messages: list[dict], kind: str) -> dict:
    """Versioned validation adapter for the train-only historical evidence view."""
    if candidate['split'] != 'validation' or kind not in {'selected', 'expanded_reference'}:
        raise TopicContractError('invalid validation evidence scope')
    cutoff = candidate['cutoff']
    source_ids, public_messages, previous = [], [], None
    for index, row in enumerate(messages):
        if row['role'] not in {'self', 'target'} or (
                row['owner_scope'], row['session_id'], row['split'], row['day']) != (
                candidate['owner_scope'], candidate['session_id'], 'validation', candidate['day']):
            raise TopicContractError('validation evidence scope mismatch')
        position = row['timestamp'], row['order']
        if (position >= (cutoff['timestamp'], cutoff['source_record_index'])
                or row['order'] >= cutoff['source_record_index']
                or (previous is not None and (position <= (previous['timestamp'], previous['order'])
                                              or row['order'] <= previous['order']))):
            raise TopicContractError('validation evidence has future or unordered messages')
        source_ids.append(row['message_id'])
        public_messages.append({'index': index, 'speaker': row['role'], 'text': row['content'],
            'kind': row['kind'], 'gap_before': bool(previous and row['order'] > previous['order'] + 1)})
        previous = row
    if not source_ids or len(set(source_ids)) != len(source_ids) or set(source_ids) & set(candidate['target_message_ids']):
        raise TopicContractError('validation evidence identity overlap')
    content = [*messages, *candidate['target_messages']]
    if scan_adjacent_messages([{'content': row['content'], 'source_record_index': row['order']}
                              for row in content])['hard_leak_count']:
        raise TopicContractError('validation_reference_privacy_hard_hit')
    if any(re.search(DEFAULT_PATTERN, row['content']) for row in content):
        raise TopicContractError('validation_reference_canary_hit')
    return {'sample_id': candidate['sample_id'], 'candidate_sha256': candidate['candidate_sha256'],
            'kind': kind, 'source_ids': source_ids,
            'case': {'past_messages': public_messages, 'reply': candidate['messages'][-1]['content']}}


def validation_payload(candidate: dict, judge: dict, budget: dict) -> dict:
    """Preserve the v4 wire contract, with a genuine validation-only scope check."""
    view = validation_view(candidate, candidate['context_messages'], 'selected')['case']
    cases = [{'index': 0, 'reply': view['reply'], 'past_messages': [
        [m['index'], m['speaker'], m['kind'], int(m['gap_before']), m['text']] for m in view['past_messages']]}]
    schema = axes.core.wire_schema(axes.core.BATCH_SCHEMA)
    messages = [{'role': 'system', 'content': axes.PROMPT + json.dumps(schema, ensure_ascii=False)},
                {'role': 'user', 'content': json.dumps({'cases': cases}, ensure_ascii=False)}]
    common = {'model': judge['model'], 'store': False}
    fmt = {'name': 'topic_reply_axes_v4', 'strict': True, 'schema': schema}
    if judge['transport'] == 'responses':
        return {**common, 'max_output_tokens': budget['max_output_tokens'], 'input': messages,
                'text': {'format': {'type': 'json_schema', **fmt}}}
    return {**common, 'max_tokens': budget['max_output_tokens'], 'messages': messages,
            'response_format': {'type': 'json_schema', 'json_schema': fmt}}


def build_population(grouped: dict, universe: dict, tokenizer, candidate_config: dict,
                     policy: dict, development: list) -> tuple[list, list, list, dict]:
    config = deepcopy(candidate_config)
    config['allowed_splits'] = ['validation']
    validator = jsonschema.Draft202012Validator(validation_schema())
    attempts, quarantined, structural_skips = [], [], Counter()
    selection = policy['selection']
    forbidden_ids = {mid for row in development for mid in row['context_message_ids'] + row['target_message_ids']}
    forbidden_sessions = {row['session_id'] for row in development}
    seen_windows = {digest(row['messages']) for row in development}
    for (sid, day), messages in sorted(grouped.items()):
        if sid in forbidden_sessions or any(m.split != 'validation' or m.message_id in forbidden_ids
                or universe.get(m.message_id) != (sid, 'validation') for m in messages):
            raise TopicContractError('development/validation identity overlap')
        try:
            validate_references(messages, universe)
            turns = merge_turns(messages, config['context']['merge_gap_seconds'])
        except TopicContractError as exc:
            structural_skips[str(exc)] += 1
            quarantined.append({'session_id': sid, 'day': day, 'kind': 'session_day', 'reason': str(exc),
                                'message_count': len(messages)})
            continue
        for index, turn in enumerate(turns):
            if turn.role != 'target':
                continue
            if index == 0 or turns[index - 1].role != 'self':
                structural_skips['no_incoming_self_turn'] += 1
                continue
            start = max(0, index - config['context']['max_history_turns'])
            key = digest({'seed': selection['seed'], 'target_ids': turn.ids})
            attempts.append((key, turns[start:index], turn, start == 0))
    attempts.sort(key=lambda item: (item[0], item[2].ids))
    accepted, evidence, target_failures = [], [], 0
    seen_targets = set()
    for _, prefix, target, complete_start in attempts[:selection['max_target_attempts']]:
        if len(accepted) >= selection['max_candidates']:
            break
        if any(mid in seen_targets for mid in target.ids):
            raise TopicContractError('duplicate validation target identity')
        seen_targets.update(target.ids)
        try:
            row = build_candidate(prefix, target, tokenizer, config, prefix_complete_start=complete_start)
            row['schema_version'] = CANDIDATE_VERSION
            row['sample_id'] = 'topicvalidation_' + digest({'target': target.ids, 'policy': digest(config)})[:24]
            row['candidate_sha256'] = digest({k: v for k, v in row.items() if k != 'candidate_sha256'})
            validator.validate(row)
            window = digest(row['messages'])
            if window in seen_windows:
                raise TopicContractError('exact_duplicate_development_or_validation_window')
        except TopicContractError as exc:
            target_failures += 1
            quarantined.append({'target_message_ids': target.ids, 'kind': 'target', 'reason': str(exc)})
            continue
        seen_windows.add(window)
        # Available past is prepared for blind reference review, never fed back
        # to the model's selected input; absence of evidence stays explicit.
        past = [asdict(message) for turn in prefix for message in turn.messages]
        try:
            view = validation_view(row, past, 'expanded_reference')
            reference = {'view': view, 'status': 'available', 'prefix_complete_start': complete_start}
        except TopicContractError as exc:
            if str(exc) not in {'validation_reference_privacy_hard_hit', 'validation_reference_canary_hit'}:
                raise
            reference = {'status': 'unavailable_privacy_or_structure', 'prefix_complete_start': complete_start}
        accepted.append(row)
        evidence.append({'sample_id': row['sample_id'], 'candidate_sha256': row['candidate_sha256'],
                         'strata': strata_for(row), 'reference': reference})
    processed = len(accepted) + target_failures
    counts = {'eligible_target_attempts': len(attempts), 'processed_target_attempts': processed,
              'candidates': len(accepted), 'quarantined_target_attempts': target_failures,
              'unprocessed_target_attempts': len(attempts) - processed,
              'quarantined_session_days': sum(r['kind'] == 'session_day' for r in quarantined)}
    assert counts['eligible_target_attempts'] == len(accepted) + target_failures + counts['unprocessed_target_attempts']
    return accepted, evidence, quarantined, {'counts': counts, 'structural_skips': dict(structural_skips)}


def write_blind_page(path: Path, candidates: list, evidence: list) -> None:
    parts = ['<!doctype html><html lang="zh"><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">',
             '<title>独立 validation 回复核验</title><style>body{max-width:960px;margin:32px auto;padding:0 20px;',
             'font:16px/1.6 sans-serif}article{border-top:1px solid #aaa;padding:20px 0}',
             'pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>',
             '<h1>独立 validation 回复核验</h1><p>无机器判定的盲审包；仅查看不会生成审核记录。</p>',
             '<p>先仅凭模型输入核验回应对象、所指是否充分、沟通价值和风险，再查看可用的更早前文。',
             '更早前文只能解释遗漏，不能让缺少必要上下文的模型输入被判完整。</p>']
    for index, (row, extra) in enumerate(zip(candidates, evidence, strict=True), 1):
        parts.extend([f'<article><h2>样本 {index}</h2>',
                      '<small>' + html.escape(row['sample_id']) + '</small>',
                      '<h3>模型输入</h3><pre>' + html.escape(row['messages'][1]['content']) + '</pre>',
                      '<h3>原始回复</h3><pre>' + html.escape(row['messages'][-1]['content']) + '</pre>'])
        reference = extra['reference']
        if reference['status'] == 'available':
            text = '\n'.join(f"[{m['index']}] {m['speaker']}: {m['text']}"
                             for m in reference['view']['case']['past_messages'])
            parts.append('<details><summary>可用前文（最多 64 轮）</summary><pre>' + html.escape(text) + '</pre></details>')
        else:
            parts.append('<p>扩展前文未通过自动检查；参考证据不足，不能补造。</p>')
        parts.append('</article>')
    parts.append('</html>')
    with path.open('x', encoding='utf-8') as handle:
        handle.write('\n'.join(parts))
    path.chmod(0o600)


def prepare_validation(*, source: Path, memory: Path, consent: Path, development: Path,
                       completed_review: Path, config_path: Path, tokenizer_path: Path,
                       output_root: Path, controlled_root: Path, base_url: str,
                       authorization_reference: str, execute: bool = False) -> dict:
    for path in (source, memory, consent, development, completed_review, output_root):
        private_path(path, controlled_root)
    for parent in (source, memory, development, completed_review):
        if output_root.resolve() == parent.resolve() or parent.resolve() in output_root.resolve().parents:
            raise TopicContractError('validation output overlaps a source')
    if not authorization_reference or not base_url:
        raise TopicContractError('validation preparation requires source route and authority bindings')
    policy = load_policy(config_path)
    if (file_digest(development / 'manifest.json') != policy['development_manifest_sha256']
            or file_digest(completed_review / 'manifest.json') != policy['completed_review_manifest_sha256']):
        raise TopicContractError('validation development evidence changed')
    dev_manifest, dev_rows = verified_pilot(development)
    completed = verify_manifest(completed_review, {'identity.json', 'report.json', 'decisions.json',
                                                  'cases.json', 'train.draft.jsonl'})
    old_report = read_json(completed_review / 'report.json')
    if (old_report['reviewed'] != 400 or old_report['status'] != 'complete'
            or any(completed.get(k) is not False for k in ('training_run', 'human_review_completed',
                                                         'formal_training_eligible', 'promotable'))):
        raise TopicContractError('validation requires completed machine-only development review')
    candidate_path = config_path.parent / policy['candidate_config']
    protocol_path = (config_path.parent / policy['protocol_document']).resolve()
    candidate_config = load_candidate_policy(candidate_path)
    review_path = config_path.parent / policy['review_config']
    review_policy = axes.load_config(review_path)
    binding = axes.binding_for(review_policy, review_path.parent / review_policy['synthetic_fixtures'], base_url)
    if binding != completed['identity']['protocol']:
        raise TopicContractError('frozen v4 review protocol changed')
    # Bind every original input before selecting or decoding validation bodies.
    for name, sha in dev_manifest['identity']['input_digests'].items():
        if file_digest(Path(name)) != sha:
            raise TopicContractError('development source dependency changed')
    source_manifest, universe, audit, _ = source_identity(source, memory, consent, candidate_config)
    if (source_manifest['manifest_sha256'] != dev_manifest['identity']['source_manifest_sha256']
            or audit != read_json(development / 'source-audit.json')
            or digest(candidate_config) != dev_manifest['identity']['policy_sha256']):
        raise TopicContractError('validation source or preprocessing differs from development')
    for name in ('source_manifest.json', 'split_manifest.json', 'lineage.jsonl'):
        original = [sha for path, sha in dev_manifest['identity']['input_digests'].items()
                    if path.endswith('/manifests/' + name)]
        if original != [file_digest(source / 'manifests' / name)]:
            raise TopicContractError('validation source manifest binding mismatch')
    original_messages = [sha for path, sha in dev_manifest['identity']['input_digests'].items()
                         if path.endswith('/redacted/messages.jsonl')]
    if original_messages != [file_digest(source / 'redacted/messages.jsonl')]:
        raise TopicContractError('validation message source changed')
    token_identity = tokenizer_identity(tokenizer_path, candidate_config['encoding']['tokenizer_revision'])
    if token_identity != dev_manifest['identity']['tokenizer']:
        raise TopicContractError('validation tokenizer differs from development')
    inputs = {str(path): file_digest(path) for path in (config_path, candidate_path, review_path, protocol_path,
        development / 'manifest.json', completed_review / 'manifest.json', consent,
        source / 'manifests/source_manifest.json', source / 'manifests/split_manifest.json',
        source / 'manifests/lineage.jsonl', source / 'redacted/messages.jsonl', memory / 'manifest.json',
        memory / 'manifests/evidence-map.jsonl', memory / 'manifests/chunks.jsonl')}
    sources = {name: file_digest(Path(__file__).with_name(name)) for name in
               ('topic_validation.py', 'topic_review.py', 'training.py', 'datasets.py')}
    sources.update(binding['source_sha256'])
    grouped, days, coverage = load_validation_days(source, universe, audit['owner_scope'], policy, candidate_config['timezone'])
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True, trust_remote_code=False)
    candidates, evidence, quarantine, report = build_population(grouped, universe, tokenizer, candidate_config, policy, dev_rows)
    counts = Counter(name for row in evidence for name in row['strata'])
    budget = policy['prospective_review']
    counter = tiktoken.get_encoding('o200k_base')
    estimates = {name: sum(len(counter.encode(json.dumps(validation_payload(row, judge, budget),
                            ensure_ascii=False))) + 256 for row in candidates) for name, judge in review_policy['judges'].items()}
    if sum(estimates.values()) > budget['max_input_tokens']:
        raise TopicContractError('validation prospective review exceeds input budget')
    insufficient = [name for name in policy['strata']['required'] if name != 'low_confidence'
                    and counts[name] < policy['strata']['minimum_count']]
    report.update(coverage=coverage, strata_counts=dict(counts), insufficient_strata=insufficient,
        low_confidence_stratum='pending_machine_review_not_a_sampling_filter',
        reference_unavailable_count=sum(r['reference']['status'] != 'available' for r in evidence),
        candidate_population_complete=len(candidates) == policy['selection']['max_candidates'],
        independent_of_development_message_ids=True, independent_of_development_sessions=True,
        exact_duplicate_development_windows=0, near_duplicate_audit_completed=False,
        test_body_materialized=False, historical_validation_exposure=(
            'memory_processing' if audit['historical_memory_messages_by_split'].get('validation') else 'not_proven_untouched'),
        historical_validation_exposed_messages=audit['historical_memory_messages_by_split'].get('validation', 0),
        final_test_ready=False, machine_review_completed=False, reference_review_completed=False,
        quality_gate_passed=False, p3_accepted=False,
        prospective_review={'planned_first_requests': len(candidates) * len(review_policy['judges']),
                            'estimated_first_input_tokens_by_judge': estimates, 'budget': budget,
                            'execution_ready': False, 'response_decoder_requires_validation_scope_adapter': True},
        **GOVERNANCE)
    identity = {'version': VERSION, 'input_digests': inputs, 'source_digests': sources, 'policy_sha256': digest(policy),
                'split_sha256': audit['split_sha256'], 'review_protocol': binding, 'tokenizer': token_identity,
                'schema_sha256': digest(validation_schema()), 'selected_days': days,
                'candidate_digests': [row['candidate_sha256'] for row in candidates],
                'authorization_reference': authorization_reference}
    output = output_root / ('topic-validation_' + digest(identity)[:20])
    result = {'status': 'planned', 'workspace': str(output), 'report': report, **GOVERNANCE}
    if not execute:
        return result
    if any(file_digest(Path(path)) != sha for path, sha in inputs.items()) or any(
            file_digest(Path(__file__).with_name(name)) != sha for name, sha in sources.items()):
        raise TopicContractError('validation dependencies changed during preparation')
    if output.exists():
        saved = verify_manifest(output, {'identity.json', 'report.json', 'validation.candidates.jsonl',
            'evidence.jsonl', 'selection.json', 'policy.json', 'candidate-schema.json', 'review.html', 'quarantine.jsonl',
            'protocol.md'})
        if saved['identity'] != identity or read_json(output / 'report.json') != report:
            raise TopicContractError('validation replay differs from frozen packet')
        return {**result, 'status': 'already_built'}
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_root.chmod(0o700)
    staging = Path(tempfile.mkdtemp(prefix='.topic-validation-', dir=output_root))
    try:
        write_json(staging / 'identity.json', identity)
        write_json(staging / 'report.json', report)
        write_json(staging / 'policy.json', policy)
        write_json(staging / 'candidate-schema.json', validation_schema())
        write_json(staging / 'source-audit.json', audit)
        shutil.copyfile(protocol_path, staging / 'protocol.md')
        (staging / 'protocol.md').chmod(0o600)
        write_json(staging / 'selection.json', {'selected_days': days, 'seed': policy['selection']['seed'],
            'method': policy['selection']['method'], 'candidate_config': {**candidate_config, 'allowed_splits': ['validation']},
            'sample_ids': [r['sample_id'] for r in candidates],
            'target_message_ids': [r['target_message_ids'] for r in candidates],
            'strata_membership': {r['sample_id']: r['strata'] for r in evidence}})
        write_jsonl(staging / 'validation.candidates.jsonl', candidates)
        write_jsonl(staging / 'evidence.jsonl', evidence)
        write_jsonl(staging / 'quarantine.jsonl', quarantine)
        write_blind_page(staging / 'review.html', candidates, evidence)
        manifest = {'schema_version': VERSION, 'identity': identity, **GOVERNANCE,
                    'output_digests': {p.name: file_digest(p) for p in sorted(staging.iterdir())}}
        manifest['manifest_sha256'] = digest(manifest)
        write_json(staging / 'manifest.json', manifest)
        if (any(file_digest(Path(path)) != sha for path, sha in inputs.items())
                or any(file_digest(Path(__file__).with_name(name)) != sha for name, sha in sources.items())
                or tokenizer_identity(tokenizer_path, candidate_config['encoding']['tokenizer_revision']) != token_identity):
            raise TopicContractError('validation dependencies changed before publication')
        _publish_directory_noreplace(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {**result, 'status': 'built'}
