"""Count-only P3 gate report joining sealed machine judgments and blind reference."""
from __future__ import annotations

from collections import Counter
import fcntl
import json
import math
from pathlib import Path
import re
import tempfile

from . import topic_blind_reference as reference
from . import topic_validation_review as review
from . import topic_validation_recovery as recovery
from ._common import digest, file_digest
from .fact_review_server import _atomic_json
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json, rows
from .topic_context import TopicContractError

METHOD = 'topic-validation-acceptance-v1'
METRICS = {'reply_link_support': .98, 'input_completeness_support': .95, 'machine_keep_support': .95}


def count_reference_metadata(path: Path, universe: dict) -> dict:
    """Count only metadata on train/validation rows; never JSON-decode message bodies."""
    pattern = lambda field: re.compile(r'"' + field + r'"\s*:\s*("(?:\\.|[^"\\])*"|null)')
    id_pattern, ref_pattern = pattern('message_id'), pattern('reply_to')
    counts = {part: Counter(messages=0, reference_field_present=0, nonnull_reply_to=0)
              for part in ('train', 'validation')}
    seen = set()
    with path.open(encoding='utf-8') as handle:
        for line in handle:
            matches = id_pattern.findall(line)
            if len(matches) != 1:
                raise TopicContractError('ambiguous reference metadata identity')
            mid = json.loads(matches[0])
            part = universe.get(mid)
            if part not in counts:
                continue
            if mid in seen:
                raise TopicContractError('duplicate reference metadata identity')
            seen.add(mid)
            refs = ref_pattern.findall(line)
            if len(refs) > 1:
                raise TopicContractError('ambiguous reply metadata')
            counts[part].update(messages=1, reference_field_present=bool(refs),
                                nonnull_reply_to=bool(refs and json.loads(refs[0])))
    if seen != {mid for mid, part in universe.items() if part in counts}:
        raise TopicContractError('reference metadata source coverage mismatch')
    return {'by_split': {k: dict(v) for k, v in counts.items()},
            'message_bodies_decoded': 0, 'test_reference_fields_scanned': False,
            'scope': 'existing_normalized_source_not_original_export'}


def proportion(values: list[bool | None]) -> dict:
    n, k = len(values), sum(v is True for v in values)
    unknown = sum(v is None for v in values)
    interval = None
    if n and unknown < n:
        z = 1.959963984540054
        p, factor = k / n, 1 + z * z / n
        center = (p + z * z / (2 * n)) / factor
        radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / factor
        interval = [max(0.0, center - radius), min(1.0, center + radius)]
    return {'numerator': k, 'denominator': n, 'unknown': unknown,
            'rate': k / n if n and unknown < n else None, 'wilson_95_descriptive': interval,
            'estimable': bool(n) and unknown < n}


def conjunction(values: list[bool | None]) -> bool | None:
    return False if False in values else (None if None in values else True)


def reference_values(candidate: dict, ref: dict | None, shared: list[str]) -> dict:
    if not ref or not ref['selected'] or not ref['expanded']:
        return {'reply_link_support': None, 'input_completeness_support': None,
                'usable': None, 'machine_keep_support': None, 'missing_evidence': None}
    labels = [ref[s]['label'] for s in ('selected', 'expanded')]
    anchor_sets = [{ref['selected_source_ids'][i] for i in label['responds_to_indices']} for label in labels]
    agreed = set.intersection(*anchor_sets)
    link = conjunction([None if label['relation'] == 'unknown' else label['relation'] == 'linked'
                        for label in labels])
    proposed = set(candidate['reply_link']['responds_to_ids'])
    link_correct = conjunction([link, bool(proposed) and proposed <= agreed if link is not None else None])
    context = conjunction([None if label['context'] == 'unknown' else label['context'] == 'sufficient'
                           for label in labels])
    value = conjunction([None if label['value'] == 'unknown' else label['value'] == 'useful' for label in labels])
    risk = conjunction([None if label['risk'] == 'unknown' else label['risk'] == 'clear' for label in labels])
    usable = conjunction([link, context, value, risk, bool(agreed) if link is not None else None])
    return {'reply_link_support': link_correct, 'input_completeness_support': context, 'usable': usable,
            'machine_keep_support': conjunction([usable, bool(set(shared) & agreed) if link is not None else None]),
            'missing_evidence': bool(labels[1]['missing_evidence_indices'])}


def summarize(candidates: list, cases: list, decisions: list, references: list, policy: dict) -> dict:
    by_id = {r['sample_id']: r for r in references}
    indexed = {(r['judge'], r['sample_id']): r for r in decisions}
    case_index = {r['sample_id']: r for r in cases}
    if len(by_id) != len(references) or set(by_id) != {c['sample_id'] for c in candidates}:
        raise TopicContractError('acceptance reference population mismatch')
    observations = []
    for candidate in candidates:
        sid = candidate['sample_id']
        ref, case = by_id[sid], case_index[sid]
        if ref['candidate_sha256'] != candidate['candidate_sha256'] or case['candidate_sha256'] != candidate['candidate_sha256']:
            raise TopicContractError('acceptance candidate digest mismatch')
        selected_ids = candidate['context_message_ids']
        if ref['selected_source_ids'] != selected_ids:
            raise TopicContractError('reference selected evidence changed')
        pair = [indexed.get((j, sid)) for j in ('gpt', 'claude')]
        group = ('missing' if not all(pair) else 'keep' if case['disposition'] == 'machine_consensus_keep'
                 else 'reject' if any(r['status'] == 'reject' for r in pair) else 'uncertain')
        observations.append({'group': group, 'strata': case['strata'],
                             **reference_values(candidate, ref, case['shared_responds_to_ids'])})
    def summarize_population(population):
        stats = {name: proportion([r[name] for r in population]) for name in
                 ('reply_link_support', 'input_completeness_support')}
        stats['machine_keep_support'] = proportion([r['machine_keep_support'] for r in population if r['group'] == 'keep'])
        stats['erroneous_reject_support'] = proportion([r['usable'] for r in population if r['group'] == 'reject'])
        stats['uncertain_usable_support'] = proportion([r['usable'] for r in population if r['group'] == 'uncertain'])
        return {'population': len(population), 'metrics': stats,
                'disposition_counts': dict(Counter(r['group'] for r in population)),
                'cross_table': {group: dict(Counter('supported' if r['usable'] is True else
                    'not_supported' if r['usable'] is False else 'unknown' for r in population if r['group'] == group))
                    for group in ('keep', 'reject', 'uncertain', 'missing')},
                'expanded_missing_evidence': sum(r['missing_evidence'] is True for r in population)}
    overall = summarize_population(observations)
    names = sorted(set(policy['strata']['required']) | {n for r in cases for n in r['strata']})
    strata = {name: summarize_population([r for r in observations if name in r['strata']]) for name in names}
    failures = []
    for name, group in [('overall', overall)] + [(n, strata[n]) for n in policy['strata']['required']]:
        for metric, threshold in METRICS.items():
            value = group['metrics'][metric]
            if value['rate'] is None or value['rate'] < threshold or not value['estimable']:
                failures.append({'population': name, 'metric': metric, 'minimum': threshold})
    insufficient = [n for n in policy['strata']['required'] if strata[n]['population'] < policy['strata']['minimum_count']]
    labels = [r[s]['label'] for r in references for s in ('selected', 'expanded') if r[s]]
    return {'overall': overall, 'strata': strata, 'insufficient_strata': insufficient,
            'metric_gate_failures': failures, 'numeric_machine_support_gates_passed': not failures,
            'completed_references': sum(bool(r['selected'] and r['expanded']) for r in references),
            'reference_issue_counts_across_stages': dict(Counter(i for label in labels for i in label['issues'])),
            'selected_to_expanded_context_changes': sum(bool(r['selected'] and r['expanded'] and
                r['selected']['label']['context'] != r['expanded']['label']['context']) for r in references),
            'reference_axis_counts': {s: {axis: dict(Counter(r[s]['label'][axis] for r in references if r[s]))
                for axis in ('relation', 'context', 'value', 'risk')} for s in ('selected', 'expanded')}}


def run_acceptance(*, packet: Path, machine: Path, blind: Path, duplicates: Path,
                   output_root: Path, controlled_root: Path, execute: bool = False) -> dict:
    for path in (packet, machine, blind, duplicates, output_root):
        private_path(path, controlled_root)
    configs = Path(__file__).resolve().parents[2] / 'configs'
    policy = review.preparation.load_policy(configs / 'topic-validation-v1.yaml')
    packet_manifest = verify_manifest(packet, {'identity.json', 'validation.candidates.jsonl', 'selection.json', 'evidence.jsonl'})
    machine_manifest = verify_manifest(machine, recovery.REQUIRED)
    blind_manifest = verify_manifest(blind, reference.REQUIRED)
    duplicate_manifest = verify_manifest(duplicates, {'policy.json', 'report.json', 'pairs.jsonl'})
    machine_identity, blind_identity = read_json(machine / 'identity.json'), read_json(blind / 'identity.json')
    for identity in (machine_identity, blind_identity):
        recovery.source_check(identity)
        if identity['inputs'].get(str(packet / 'manifest.json')) != file_digest(packet / 'manifest.json'):
            raise TopicContractError('acceptance inputs do not share frozen packet')
    if (machine_manifest['identity'] != machine_identity or blind_manifest['identity'] != blind_identity
            or read_json(machine / 'decisions.json')['identity'] != machine_identity
            or read_json(blind / 'references.json')['identity'] != blind_identity
            or blind_identity['scope'] != 'private'):
        raise TopicContractError('acceptance requires sealed private review identity')
    candidates = list(rows(packet / 'validation.candidates.jsonl'))
    if [c['candidate_sha256'] for c in candidates] != packet_manifest['identity']['candidate_digests']:
        raise TopicContractError('acceptance frozen candidate order changed')
    for candidate in candidates:
        review.validate_candidate(candidate)
    decisions = read_json(machine / 'decisions.json')['decisions']
    references = read_json(blind / 'references.json')['references']
    # Replay blind labels and inherited references with dispatch disabled.
    from . import topic_blind_reference_opus as opus
    from . import topic_blind_reference_recovery as reference_recovery
    evidence = list(rows(packet / 'evidence.jsonl'))
    if [r['sample_id'] for r in evidence] != [c['sample_id'] for c in candidates]:
        raise TopicContractError('acceptance evidence order changed')
    reference_cases = []
    for candidate, item in zip(candidates, evidence):
        reference_cases.append({'sample_id': candidate['sample_id'], 'candidate_sha256': candidate['candidate_sha256'],
            'selected': review.preparation.validation_view(candidate, candidate['context_messages'], 'selected'),
            'reference': item['reference']})
    old_config = opus.load_config(configs / 'topic-blind-reference-opus-v1.yaml')
    if blind_identity['method'] == opus.METHOD:
        replayed, _ = reference_recovery.replay_rows(blind, reference_cases, old_config)
    elif blind_identity['method'] == reference_recovery.METHOD:
        reference_parent = Path(blind_identity['parent'])
        private_path(reference_parent, controlled_root)
        if blind_identity['inputs'].get(str(reference_parent / 'manifest.json')) != file_digest(reference_parent / 'manifest.json'):
            raise TopicContractError('reference recovery parent binding changed')
        inherited_references, _ = reference_recovery.replay_rows(reference_parent, reference_cases, old_config)
        if digest(inherited_references) != blind_identity['inherited_references_sha256']:
            raise TopicContractError('reference inherited digest changed')
        replayed, _ = reference_recovery.replay_rows(blind, [reference_recovery.explicit_indices(c) for c in reference_cases],
            reference_recovery.load_config(configs / 'topic-blind-reference-index-recovery-v1.yaml'),
            inherited=inherited_references)
    else:
        raise TopicContractError('acceptance requires frozen reference method')
    if references != replayed or blind_identity['model'] != old_config['model']:
        raise TopicContractError('acceptance blind raw reconstruction changed')
    # Both inherited review chains are immutable and independently reconstructed.
    if machine_identity['scope'] != 'topic-validation-completion-v1':
        raise TopicContractError('acceptance requires completed review chain')
    parent = Path(read_json(machine / 'report.json')['parent'])
    private_path(parent, controlled_root)
    consent_paths = [Path(p) for p in machine_identity['inputs'] if Path(p).name == 'consent-c-001.json']
    if len(consent_paths) != 1:
        raise TopicContractError('acceptance consent binding missing')
    review.load_packet(packet, review.load_config(configs / 'topic-validation-review-v1.yaml'),
                       policy, machine_identity['protocol'], consent_paths[0])
    recovery_config = recovery.load_config(configs / 'topic-validation-recovery-v1.yaml')
    parent_manifest = verify_manifest(parent, recovery.REQUIRED)
    parent_identity = read_json(parent / 'identity.json')
    if (parent_manifest['identity'] != parent_identity
            or file_digest(parent / 'manifest.json') != machine_identity['parent_manifest_sha256']):
        raise TopicContractError('acceptance recovery parent changed')
    original = Path(read_json(parent / 'report.json')['parent'])
    private_path(original, controlled_root)
    selection = read_json(packet / 'selection.json')
    _, original_decisions, _ = recovery.verify_parent(original, candidates, selection['strata_membership'],
                                                      policy, recovery_config)
    inherited, _, _ = recovery.reconstruct(parent, parent_identity, candidates, original_decisions)
    final_decisions, _, _ = recovery.reconstruct(machine, machine_identity, candidates, inherited)
    if decisions != final_decisions or read_json(parent / 'decisions.json')['decisions'] != inherited:
        raise TopicContractError('acceptance machine raw reconstruction changed')
    selection = read_json(packet / 'selection.json')
    reconstructed = review.summarize(candidates, decisions, selection['strata_membership'], policy, machine_identity['models'])
    cases = reconstructed.pop('cases')
    if read_json(machine / 'cases.json')['cases'] != cases:
        raise TopicContractError('acceptance machine case reconstruction mismatch')
    duplicate_report = read_json(duplicates / 'report.json')
    duplicate_policy = read_json(duplicates / 'policy.json')
    if duplicate_policy['packet_manifest_sha256'] != file_digest(packet / 'manifest.json'):
        raise TopicContractError('acceptance duplicate population changed')
    report = summarize(candidates, cases, decisions, references, policy)
    pending_ids = {r['sample_id'] for r in references if not (r['selected'] and r['expanded'])}
    report['pending_blind_reference_count'] = len(pending_ids)
    packet_inputs = packet_manifest['identity']['input_digests']
    source_paths = [Path(p) for p in packet_inputs if p.endswith('/redacted/messages.jsonl')]
    if len(source_paths) != 1:
        raise TopicContractError('reference metadata source is ambiguous')
    source = source_paths[0].parent.parent
    split = read_json(source / 'manifests/split_manifest.json')['session_ids_by_split']
    sessions = {sid: part for part, ids in split.items() for sid in ids}
    universe = {}
    for row in rows(source / 'manifests/lineage.jsonl'):
        if row['stage'] == 'session':
            universe.update({mid: sessions[row['object_id']] for mid in row['source_message_ids']})
    report['reference_metadata_audit'] = count_reference_metadata(source_paths[0], universe)
    machine_complete = len(decisions) == len(candidates) * 2
    reference_complete = report['completed_references'] == len(candidates)
    duplicate_pass = (duplicate_report['audit_completed_for_declared_scope']
        and not duplicate_report['cross_split_flagged_pairs'] and not duplicate_report['within_validation_flagged_pairs'])
    reasons = ([] if machine_complete else ['incomplete_machine_judgments']) + (
        [] if reference_complete else ['incomplete_blind_reference']) + (
        [] if duplicate_pass else ['duplicate_gate']) + (
        [] if not report['insufficient_strata'] else ['insufficient_critical_strata']) + (
        [] if not report['metric_gate_failures'] else ['machine_support_below_frozen_threshold'])
    reasons.append('machine_reference_does_not_grant_formal_qualification')
    report.update(machine_review_completed=machine_complete, valid_machine_judgments=len(decisions),
        reference_review_completed=reference_complete, near_duplicate_audit_completed=duplicate_pass,
        full_training_population_audited=False, blocker_reasons=reasons,
        decision='rework_required' if len(reasons) > 1 else 'qualification_review_required',
        p3_accepted=False, p4_ready=False, p5_ready=False, p6_ready=False,
        final_test_requires_new_unexposed_population=True,
        reference_input_annotation_versions=({'original': sum(bool(r['selected'] and r['expanded']) for r in inherited_references),
            'explicit_self_indices': sum(not (r['selected'] and r['expanded']) for r in inherited_references)}
            if blind_identity['method'] == reference_recovery.METHOD
            else {'original': len(candidates)}),
        reference_protocol_calibrated_for_real_precision=False,
        intervals_are_population_guarantees=False, same_gateway_error_correlation_possible=True,
        same_family_as_claude_judge=True, **reference.GOVERNANCE)
    protocol = Path(__file__).resolve().parents[2] / 'docs/daily-topic-sft-blind-reference-protocol.md'
    identity = {'method': METHOD, 'inputs': {str(p): file_digest(p) for p in (
        packet / 'manifest.json', machine / 'manifest.json', blind / 'manifest.json',
        duplicates / 'manifest.json', configs / 'topic-validation-v1.yaml', protocol)},
        'source_digests': {name: file_digest(Path(__file__).with_name(name)) for name in (
            Path(__file__).name, 'topic_blind_reference.py', 'topic_blind_reference_opus.py',
            'topic_blind_reference_recovery.py',
            'topic_validation_recovery.py', 'topic_validation_budget.py', 'topic_validation_review.py',
            'topic_validation.py', 'topic_adjudication.py', '_common.py')}}
    directory = output_root / (METHOD + '_' + digest(identity)[:20])
    result = {'status': 'planned', 'workspace': str(directory), 'report': report}
    if not execute:
        return result
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    with (directory / 'run.lock').open('a') as lock:
        (directory / 'run.lock').chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (directory / 'manifest.json').exists():
            manifest = verify_manifest(directory, {'identity.json', 'report.json', 'pending-review.html'})
            if manifest['identity'] != identity or read_json(directory / 'report.json') != report:
                raise TopicContractError('acceptance replay changed')
        else:
            _atomic_json(directory / 'identity.json', identity)
            _atomic_json(directory / 'report.json', report)
            with tempfile.TemporaryDirectory(prefix='.pending-', dir=directory) as staging:
                page = Path(staging) / 'pending-review.html'
                review.preparation.write_blind_page(page,
                    [c for c in candidates if c['sample_id'] in pending_ids],
                    [e for e in evidence if e['sample_id'] in pending_ids])
                page.replace(directory / 'pending-review.html')
            recovery.source_check(machine_identity)
            recovery.source_check(blind_identity)
            manifest = {'identity': identity, **reference.GOVERNANCE,
                'output_digests': {n: file_digest(directory / n) for n in ('identity.json', 'report.json', 'pending-review.html')}}
            manifest['manifest_sha256'] = digest(manifest)
            _atomic_json(directory / 'manifest.json', manifest)
    return {**result, 'status': 'complete'}
