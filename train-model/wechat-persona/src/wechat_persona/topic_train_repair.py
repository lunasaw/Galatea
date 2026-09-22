"""Immutable, offline train repair packet; no validation relabeling or training."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
import shutil
import tempfile

import yaml

from ._common import digest, file_digest
from .datasets import _publish_directory_noreplace
from .topic_adjudication import verify_manifest
from .topic_atomic_evidence import bind_atomic_evidence, replay_train_review
from .topic_candidates import (private_path, read_json, rows, tokenizer_identity, verified_pilot,
                               write_json, write_jsonl)
from .topic_context import AtomicMessage, TopicContractError, merge_turns
from .topic_context_contiguous import POLICY_VERSION, build_contiguous_candidate
from .topic_quote_provenance import audit_quotes
from .topic_repair_contract import SCHEMA_PATH, validate_repaired_rows


GOVERNANCE = {'training_run': False, 'human_review_completed': False,
              'formal_training_eligible': False, 'promotable': False}


def load_config(path: Path) -> dict:
    policy = yaml.safe_load(path.read_text(encoding='utf-8'))
    expected = {'schema_version': 'topic-train-repair-config-v1', 'allowed_splits': ['train'], 'population': 200,
                'context': {'policy_version': POLICY_VERSION, 'max_context_turns': 16, 'max_history_turns': 64,
                            'merge_gap_seconds': 120, 'cross_session': False, 'cross_day': False},
                'anchor_rule': 'intersect_replayed_dual_machine_self_anchors',
                'required_context_rule': 'union_replayed_dual_machine_required_context',
                'reuse_verdict_for_changed_context': False, 'external_requests': 0, 'governance': GOVERNANCE}
    if policy != expected:
        raise TopicContractError('invalid frozen train repair policy')
    return policy


def compare_contexts(pilot: Path, candidates: list[dict], cases: list[dict],
                     tokenizer, config: dict) -> tuple[list, list]:
    """Preserve all target attempts, including failures; never replace a target."""
    grouped = {}
    for bundle in rows(pilot / 'daily-bundles.jsonl'):
        if bundle['split'] != 'train' or any(row['split'] != 'train' for row in bundle['messages']):
            raise TopicContractError('context repair bundle outside train')
        key = bundle['session_id'], bundle['day']
        if key in grouped:
            raise TopicContractError('duplicate train day bundle')
        grouped[key] = merge_turns([AtomicMessage(**m) for m in bundle['messages']],
                                   config['context']['merge_gap_seconds'])
    cases_by_id = {row['sample_id']: row for row in cases}
    if len(cases_by_id) != len(cases) or set(cases_by_id) != {row['sample_id'] for row in candidates}:
        raise TopicContractError('context repair review population mismatch')
    outputs, records = [], []
    for old in candidates:
        case = cases_by_id[old['sample_id']]
        turns = grouped[(old['session_id'], old['day'])]
        indices = [i for i, turn in enumerate(turns) if turn.ids == old['target_message_ids']]
        if len(indices) != 1:
            raise TopicContractError('frozen target not found uniquely')
        index = indices[0]
        start = max(0, index - config['context']['max_history_turns'])
        record = {'parent_sample_id': old['sample_id'], 'parent_candidate_sha256': old['candidate_sha256'],
                  'target_message_ids': old['target_message_ids'], 'previous_disposition': case['disposition'],
                  'prior_hard_risks': case['prior_hard_risks'], 'fresh_review_required': True,
                  'old_quality_decisions_transferred': False, **GOVERNANCE}
        try:
            new = build_contiguous_candidate(turns[start:index], turns[index], tokenizer, config,
                                              parent=old, prefix_complete_start=start == 0)
        except TopicContractError as exc:
            # Reasons are fixed component enums, never message content.
            record.update(status='quarantined', reason=str(exc))
        else:
            new['prior_hard_risks'] = case['prior_hard_risks']
            new['candidate_sha256'] = digest({k: v for k, v in new.items() if k != 'candidate_sha256'})
            old_ids, new_ids = set(old['context_message_ids']), set(new['context_message_ids'])
            record.update(status='built', candidate_sha256=new['candidate_sha256'],
                          input_changed=old['messages'][:-1] != new['messages'][:-1],
                          added_context_messages=len(new_ids - old_ids), removed_context_messages=len(old_ids - new_ids),
                          old_review_required_context_missing=sorted(set(case['required_context_ids']) - new_ids),
                          old_review_anchors_missing=sorted(set(case['shared_responds_to_ids']) - new_ids),
                          old_prompt_tokens=old['tokens']['prompt_tokens'],
                          new_prompt_tokens=new['tokens']['prompt_tokens'],
                          stop_reason=new['topic']['stop_reason'])
            outputs.append(new)
        records.append(record)
    return outputs, records


def publish_packet(output: Path, identity: dict, artifacts: dict) -> None:
    if output.exists():
        saved = verify_manifest(output, set(artifacts))
        if saved['identity'] != identity:
            raise TopicContractError('existing train repair identity changed')
        for name, value in artifacts.items():
            existing = list(rows(output / name)) if name.endswith('.jsonl') else read_json(output / name)
            if existing != value:
                raise TopicContractError('existing train repair replay differs')
        return
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.parent.chmod(0o700)
    staging = Path(tempfile.mkdtemp(prefix='.topic-train-repair-', dir=output.parent))
    try:
        for name, value in artifacts.items():
            if Path(name).name != name:
                raise TopicContractError('invalid train repair artifact name')
            (write_jsonl if name.endswith('.jsonl') else write_json)(staging / name, value)
        manifest = {'schema_version': 'topic-train-repair-manifest-v1', 'identity': identity, **GOVERNANCE,
                    'output_digests': {name: file_digest(staging / name) for name in artifacts}}
        manifest['manifest_sha256'] = digest(manifest)
        write_json(staging / 'manifest.json', manifest)
        _publish_directory_noreplace(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def run_repair(*, pilot: Path, review: Path, raw: Path, source: Path, consent: Path,
               config_path: Path, review_config: Path, tokenizer_path: Path,
               output_root: Path, controlled_root: Path, base_url: str, execute: bool = False) -> dict:
    for path in (pilot, review, raw, source, consent, output_root):
        private_path(path, controlled_root)
    for path in (pilot, review, raw, source, consent):
        if output_root == path or path in output_root.parents or output_root in path.parents:
            raise TopicContractError('train repair output overlaps source')
    policy = load_config(config_path)
    pilot_manifest, originals = verified_pilot(pilot)
    config = deepcopy(read_json(pilot / 'policy.json'))
    config.update(schema_version='daily-topic-sft-config-v2', context=policy['context'])
    token_identity = tokenizer_identity(tokenizer_path, config['encoding']['tokenizer_revision'])
    source_manifest = read_json(source / 'manifests/source_manifest.json')
    if (len(originals) != policy['population'] or token_identity != pilot_manifest['identity']['tokenizer']
            or source_manifest['manifest_sha256'] != pilot_manifest['identity']['source_manifest_sha256']
            or source_manifest['manifest_sha256'] != digest({k: v for k, v in source_manifest.items() if k != 'manifest_sha256'})):
        raise TopicContractError('train repair source population or tokenizer changed')
    candidates, decisions, cases, receipt = replay_train_review(pilot=pilot, review=review,
        config_path=review_config, controlled_root=controlled_root, base_url=base_url)
    if candidates != originals or str(consent) not in receipt['inputs']:
        raise TopicContractError('train repair candidate or consent mismatch')
    inputs = {**receipt['inputs'], **{str(path): file_digest(path) for path in (
        raw, consent, config_path, review_config, SCHEMA_PATH,
        Path(__file__).resolve().parents[2] / 'scripts/repair_topic_train_drafts.py', source / 'manifests/source_manifest.json',
        source / 'manifests/split_manifest.json', source / 'manifests/lineage.jsonl', source / 'redacted/messages.jsonl')}}
    frozen_inputs = pilot_manifest['identity']['input_digests']
    for path in (source / 'manifests/source_manifest.json', source / 'manifests/split_manifest.json',
                 source / 'manifests/lineage.jsonl', source / 'redacted/messages.jsonl', consent):
        if inputs[str(path)] != frozen_inputs.get(str(path)):
            raise TopicContractError('train repair frozen source changed')
    split = read_json(source / 'manifests/split_manifest.json')
    if (digest(split['session_ids_by_split']) != split['split_sha256']
            or split['split_sha256'] != source_manifest['split_sha256']
            or split['manifest_sha256'] != digest({k: v for k, v in split.items()
                                                   if k not in {'split_sha256', 'manifest_sha256'}})):
        raise TopicContractError('train repair split identity changed')
    implementation = {name: file_digest(Path(__file__).with_name(name)) for name in (
        'topic_train_repair.py', 'topic_quote_provenance.py', 'topic_atomic_evidence.py',
        'topic_context_contiguous.py', 'topic_repair_contract.py', 'datasets.py',
        'topic_axes_singleton_v4.py', 'topic_axes_recovery_v4.py')}
    identity = {'method': 'topic-train-repair-v1', 'inputs': inputs, 'policy_sha256': digest(policy),
                'implementation_sha256': implementation, 'tokenizer': token_identity,
                'target_population_sha256': digest([r['target_message_ids'] for r in candidates])}
    output = output_root / ('topic-train-repair_' + digest(identity)[:20])
    plan = {'status': 'planned', 'output_dir': str(output), 'population': len(candidates),
            'external_requests': 0, 'training_ready': False, **GOVERNANCE}
    if not execute:
        return plan
    quote_report = audit_quotes(raw=raw, source=source, consent=consent)
    indexed = {(r['judge'], r['sample_id']): r for r in decisions}
    by_id = {r['sample_id']: r for r in candidates}
    draft = [bind_atomic_evidence(by_id[c['sample_id']], [indexed[name, c['sample_id']] for name in ('gpt', 'claude')],
                                 c, receipt['review_manifest_sha256']) for c in cases if c['disposition'] == 'selected']
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True)
    new_contexts, comparison = compare_contexts(pilot, candidates, cases, tokenizer, config)
    validate_repaired_rows([*draft, *new_contexts])
    link_changes = Counter()
    for row in draft:
        old, new = set(row['reply_link_hypothesis']['responds_to_ids']), set(row['reply_link']['responds_to_ids'])
        change = ('unchanged' if old == new else 'narrowed' if new < old else
                  'overlapping_changed' if old & new else 'redirected')
        link_changes[change] += 1
    built = [r for r in comparison if r['status'] == 'built']
    report = {**plan, 'status': 'built', 'selected_atomic_drafts': len(draft),
              'selection_disposition_counts': dict(Counter(c['disposition'] for c in cases)),
              'atomic_anchor_change_counts': dict(link_changes),
              'review_replay': {k: v for k, v in receipt.items() if k != 'inputs'},
              'context_comparison': {'population': len(comparison), 'built': len(built),
                  'quarantined': len(comparison) - len(built), 'input_changed': sum(c['input_changed'] for c in built),
                  'added_context_messages': sum(c['added_context_messages'] for c in built),
                  'removed_context_messages': sum(c['removed_context_messages'] for c in built),
                  'cases_missing_old_required_context': sum(bool(c['old_review_required_context_missing']) for c in built),
                  'cases_missing_old_anchors': sum(bool(c['old_review_anchors_missing']) for c in built),
                  'quarantine_reasons': dict(Counter(c['reason'] for c in comparison if c['status'] == 'quarantined')),
                  'stop_reasons': dict(Counter(c['stop_reason'] for c in built))},
              'selected_inputs_and_targets_unchanged': True, 'fresh_context_quality_review_completed': False,
              'independent_quality_validation_completed': False, 'old_validation_changed': False,
              'replacement_targets_used': False, 'final_test_body_decoded': False,
              'p3_status': 'rework_requires_new_quality_validation'}
    artifacts = {'train.atomic.draft.jsonl': draft, 'context.candidates.jsonl': new_contexts,
                 'selection.json': {'cases': cases}, 'context-comparison.json': {'cases': comparison},
                 'quote-provenance.json': quote_report, 'policy.json': config, 'report.json': report}
    if (any(file_digest(Path(path)) != sha for path, sha in inputs.items())
            or any(file_digest(Path(__file__).with_name(name)) != sha for name, sha in implementation.items())
            or tokenizer_identity(tokenizer_path, config['encoding']['tokenizer_revision']) != token_identity):
        raise TopicContractError('train repair inputs changed during execution')
    publish_packet(output, identity, artifacts)
    return {**report, 'manifest_sha256': file_digest(output / 'manifest.json'), 'quote_provenance': quote_report}
