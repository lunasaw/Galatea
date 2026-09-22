"""Compare data-construction revisions on one frozen train target population."""
from __future__ import annotations

from collections import Counter
from math import sqrt
from pathlib import Path

from ._common import digest, file_digest
from .topic_candidates import private_path, read_json, rows, verified_pilot, write_json
from .topic_context import AtomicMessage, TopicContractError, merge_turns


def verified_review(pilot: Path, review: Path, candidates: list[dict]) -> tuple[dict, dict]:
    report = read_json(review / 'report.json')
    payload = read_json(review / 'decisions.json')
    decisions = payload['decisions']
    identity = payload['identity']
    if (report['status'] != 'complete' or report['decisions_sha256'] != digest(decisions)
            or identity['pilot_manifest_sha256'] != file_digest(pilot / 'manifest.json')):
        raise TopicContractError('incomplete or mismatched comparison review')
    indexed = {row['sample_id']: row for row in decisions}
    if len(indexed) != len(decisions) or set(indexed) != {row['sample_id'] for row in candidates}:
        raise TopicContractError('review population mismatch')
    for row in candidates:
        if indexed[row['sample_id']]['candidate_sha256'] != row['candidate_sha256']:
            raise TopicContractError('review candidate digest mismatch')
    return identity, indexed


def proportion(count: int, total: int) -> dict:
    p = count / total
    z = 1.959963984540054
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return {'count': count, 'denominator': total, 'rate': p,
            'wilson_95': [max(0, center - radius), min(1, center + radius)]}


def compare_population(before: list[dict], after: list[dict], before_decisions: dict,
                       after_decisions: dict, quarantine: list[dict]) -> dict:
    old = {tuple(row['target_message_ids']): row for row in before}
    new = {tuple(row['target_message_ids']): row for row in after}
    excluded = {tuple(row['target_message_ids']): row for row in quarantine}
    if (not old or len(old) != len(before) or len(new) != len(after)
            or len(excluded) != len(quarantine) or set(new) & set(excluded)
            or set(old) != set(new) | set(excluded)):
        raise TopicContractError('comparison must account for every frozen target without replacements')
    groups = {'changed_input': [], 'unchanged_input': []}
    statuses = Counter()
    for key, row in old.items():
        previous = before_decisions[row['sample_id']]
        if key not in new:
            statuses[previous['status'] + '->quarantine'] += 1
            continue
        current = new[key]
        if (row['target_messages'] != current['target_messages']
                or row['messages'][-1] != current['messages'][-1]
                or row['messages'][0] != current['messages'][0]):
            raise TopicContractError('comparison target or prompt contract changed')
        decision = after_decisions[current['sample_id']]
        group = 'unchanged_input' if row['messages'] == current['messages'] else 'changed_input'
        groups[group].append((previous, decision))
        statuses[previous['status'] + '->' + decision['status']] += 1
    metrics = {}
    for metric in ('reply_link_correct', 'context_complete'):
        previous_count = sum(before_decisions[row['sample_id']][metric] for row in before)
        current_count = sum(after_decisions[row['sample_id']][metric] for row in after)
        metrics[metric] = {'before': proportion(previous_count, len(old)),
                           'after': proportion(current_count, len(old)),
                           'delta_percentage_points': 100 * (current_count - previous_count) / len(old),
                           'paired_groups': {}}
        for group, pairs in groups.items():
            metrics[metric]['paired_groups'][group] = {
                'count': len(pairs),
                'false_to_true': sum(not a[metric] and b[metric] for a, b in pairs),
                'true_to_false': sum(a[metric] and not b[metric] for a, b in pairs),
            }
    return {
        'frozen_targets': len(old), 'reviewed_after': len(new), 'quarantined_after': len(excluded),
        'replacement_targets': 0, 'input_counts': {key: len(value) for key, value in groups.items()},
        'status_transitions': dict(statuses), 'metrics': metrics,
        'before_decisions': dict(Counter(row['status'] for row in before_decisions.values())),
        'after_decisions': dict(Counter(row['status'] for row in after_decisions.values())),
        'machine_thresholds': {
            'reply_link_0_98_passed': metrics['reply_link_correct']['after']['rate'] >= .98,
            'context_complete_0_95_passed': metrics['context_complete']['after']['rate'] >= .95,
        },
        'quality_gate_passed': False,
        'claim': 'paired_train_machine_audit_only_not_independent_precision_or_validation',
        'uncertainty': 'Wilson intervals exclude judge bias; unchanged-input flips measure judge variability',
    }


def remaining_context_coverage(pilot: Path, candidates: list[dict], decisions: dict) -> dict:
    policy = read_json(pilot / 'policy.json')
    prefixes = {}
    for bundle in rows(pilot / 'daily-bundles.jsonl'):
        turns = merge_turns([AtomicMessage(**row) for row in bundle['messages']],
                            policy['context']['merge_gap_seconds'])
        for index, turn in enumerate(turns):
            if turn.role == 'target':
                prefixes[tuple(turn.ids)] = turns[:index]
    counts = Counter()
    for row in candidates:
        decision = decisions[row['sample_id']]
        if decision['reply_link_correct'] and decision['context_complete']:
            continue
        counts['link_or_context_failed'] += 1
        prefix = prefixes[tuple(row['target_message_ids'])]
        available = {mid for turn in prefix for mid in turn.ids}
        selected = set(row['context_message_ids'])
        counts['all_available_past_selected' if available <= selected else 'additional_past_available'] += 1
        if len(prefix) <= 1:
            counts['at_session_day_start'] += 1
        if len(prefix) > policy['context']['max_history_turns']:
            counts['history_limit_reached'] += 1
    return dict(counts)


def compare_pilots(*, before: Path, after: Path, before_review: Path, after_review: Path,
                   output_root: Path, controlled_root: Path) -> dict:
    for path in (before, after, before_review, after_review, output_root):
        private_path(path, controlled_root)
    if any(path == output_root or path in output_root.parents
           for path in (before, after, before_review, after_review)):
        raise TopicContractError('comparison output must be outside its inputs')
    old_manifest, old = verified_pilot(before)
    new_manifest, new = verified_pilot(after)
    old_identity, old_decisions = verified_review(before, before_review, old)
    new_identity, new_decisions = verified_review(after, after_review, new)
    if (new_manifest['identity'].get('reference_pilot_manifest_sha256') != file_digest(before / 'manifest.json')
            or new_manifest['identity'].get('frozen_target_population_sha256') != digest([row['target_message_ids'] for row in old])
            or old_manifest['identity']['tokenizer'] != new_manifest['identity']['tokenizer']
            or any(old_identity[key] != new_identity[key]
                   for key in ('method', 'prompt_sha256', 'schema_sha256', 'model_requested', 'endpoint_sha256', 'policy_sha256'))):
        raise TopicContractError('comparison population/tokenizer/judge protocol mismatch')
    report = compare_population(old, new, old_decisions, new_decisions, list(rows(after / 'quarantine.jsonl')))
    report['remaining_failure_context_coverage'] = remaining_context_coverage(after, new, new_decisions)
    report['strata'] = read_json(after_review / 'report.json')['strata']
    report['input_digests'] = {str(path): file_digest(path) for path in (
        before / 'manifest.json', after / 'manifest.json', before_review / 'report.json',
        before_review / 'decisions.json', after_review / 'report.json', after_review / 'decisions.json')}
    report.update(schema_version='topic-pilot-comparison-v1', training_run=False,
                  human_review_completed=False, formal_training_eligible=False,
                  implementation_sha256=file_digest(Path(__file__)))
    path = output_root / ('topic-comparison_' + digest(report)[:20] + '.json')
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_root.chmod(0o700)
    if path.exists():
        if read_json(path) != report:
            raise TopicContractError('existing comparison changed')
    else:
        write_json(path, report)
    return {'report_path': str(path), 'report_sha256': file_digest(path), 'report': report}


def report_context_repair(*, before: Path, after: Path, audit: Path,
                          output_root: Path, controlled_root: Path) -> dict:
    """Check structural restoration; never transfer an old quality decision."""
    for path in (before, after, audit, output_root):
        private_path(path, controlled_root)
    if any(path == output_root or path in output_root.parents for path in (before, after, audit)):
        raise TopicContractError('repair report must be outside inputs')
    _, old = verified_pilot(before)
    new_manifest, new = verified_pilot(after)
    audit_manifest = read_json(audit / 'manifest.json')
    if (audit_manifest['manifest_sha256'] != digest({k: v for k, v in audit_manifest.items() if k != 'manifest_sha256'})
            or audit_manifest['identity']['pilot_manifest_sha256'] != file_digest(before / 'manifest.json')
            or any(Path(name).name != name or file_digest(audit / name) != sha
                   for name, sha in audit_manifest['output_digests'].items())
            or new_manifest['identity'].get('reference_pilot_manifest_sha256') != file_digest(before / 'manifest.json')):
        raise TopicContractError('repair report input binding mismatch')
    old_by_id = {row['sample_id']: row for row in old}
    new_by_target = {tuple(row['target_message_ids']): row for row in new}
    if len(old) != len(new) or {tuple(row['target_message_ids']) for row in old} != set(new_by_target):
        raise TopicContractError('repair must preserve all frozen targets')
    changed = 0
    for row in old:
        current = new_by_target[tuple(row['target_message_ids'])]
        if (row['target_messages'] != current['target_messages']
                or row['messages'][-1] != current['messages'][-1]
                or row['messages'][0] != current['messages'][0]):
            raise TopicContractError('repair changed a target or prompt contract')
        changed += row['messages'][1] != current['messages'][1]
    audit_report = read_json(audit / 'report.json')
    if audit_report['status'] != 'complete':
        raise TopicContractError('repair diagnosis incomplete')
    cases = []
    for case in audit_report['diagnosis_cases']:
        if case['cause'] != 'machine_suggested_context_omission':
            continue
        previous = old_by_id[case['sample_id']]
        current = new_by_target[tuple(previous['target_message_ids'])]
        missing = set(case['missing_evidence_ids'])
        recovered = missing & set(current['context_message_ids'])
        cases.append({'before_sample_id': previous['sample_id'], 'after_sample_id': current['sample_id'],
                      'missing_evidence_count': len(missing), 'restored_evidence_count': len(recovered),
                      'remaining_evidence_ids': sorted(missing - recovered),
                      'prefix_complete_start': current['topic'].get('prefix_complete_start'),
                      'prompt_tokens': current['tokens']['prompt_tokens']})
    identity = {str(path): file_digest(path) for path in (before / 'manifest.json', after / 'manifest.json', audit / 'manifest.json')}
    report = {'schema_version': 'topic-context-repair-report-v1', 'input_digests': identity,
              'implementation_sha256': file_digest(Path(__file__)), 'frozen_targets': len(old),
              'changed_input_count': changed, 'machine_omission_cases': len(cases),
              'fully_restored_cases': sum(row['restored_evidence_count'] == row['missing_evidence_count'] for row in cases),
              'cases': cases, 'new_quality_review_completed': False, 'old_quality_decisions_transferred': False,
              'human_review_completed': False, 'formal_training_eligible': False, 'training_run': False}
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_root.chmod(0o700)
    path = output_root / ('topic-context-repair_' + digest(report)[:20] + '.json')
    if path.exists():
        if read_json(path) != report:
            raise TopicContractError('existing context repair report changed')
    else:
        write_json(path, report)
    return {'report_path': str(path), 'report_sha256': file_digest(path), 'report': report}
