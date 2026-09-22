"""Label-blind near-duplicate diagnostics for frozen development/validation packets."""
from __future__ import annotations

from itertools import combinations, product
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata

import yaml

from ._common import digest, file_digest
from .datasets import _publish_directory_noreplace
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json, rows, verified_pilot, write_json, write_jsonl
from .topic_context import TopicContractError
from .topic_validation_protocol import validate_candidate


GOVERNANCE = {'external_requests': 0, 'training_run': False, 'existing_labels_changed': False,
              'selection_changed': False, 'human_review_completed': False,
              'formal_training_eligible': False, 'promotable': False}


def load_policy(path: Path) -> dict:
    policy = yaml.safe_load(path.read_text(encoding='utf-8'))
    expected = {'schema_version': 'topic-validation-duplicate-audit-v1',
        'packet_manifest_sha256': 'afe3e682a9da34007921991c3f34ad228d398612d9a74c4dfee7bb66558be335',
        'development_manifest_sha256': '544b9fd8586b2fd26049eacd4ee0e65dc113edfa6c0224d220fdab0ce0d97407',
        'normalization': 'unicode_nfkc_casefold_collapse_whitespace_preserve_roles',
        'character_ngram': 5, 'short_text_characters': 20, 'minimum_context_jaccard': .9,
        'minimum_reply_jaccard': .9, 'short_text_requires_exact_match': True,
        'maximum_population_per_split': 200,
        'scope': 'frozen_200_development_by_200_validation_and_within_validation', 'governance': GOVERNANCE}
    if policy != expected:
        raise TopicContractError('invalid frozen duplicate audit policy')
    return policy


def normalized(text: str) -> str:
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', text).casefold()).strip()


def features(row: dict, policy: dict) -> tuple:
    # Exclude the common system instruction. Keep speaker labels and message
    # order in the actual serialized model input; no future/reference expansion.
    values = [normalized(row['messages'][1]['content']), normalized(row['messages'][-1]['content'])]
    n = policy['character_ngram']
    return tuple((text, {text[i:i + n] for i in range(len(text) - n + 1)}) for text in values)


def similarity(left: tuple, right: tuple, policy: dict) -> float:
    a, grams_a = left
    b, grams_b = right
    if a == b:
        return 1.0
    if min(len(a), len(b)) < policy['short_text_characters']:
        return 0.0
    return len(grams_a & grams_b) / len(grams_a | grams_b)


def compare(development: list, validation: list, policy: dict) -> tuple[list, dict]:
    if (not development or not validation or max(len(development), len(validation)) > policy['maximum_population_per_split']
            or any(r['split'] != 'train' for r in development) or any(r['split'] != 'validation' for r in validation)):
        raise TopicContractError('duplicate audit requires bounded train/validation inputs')
    cached = {r['sample_id']: features(r, policy) for r in development + validation}
    if len(cached) != len(development) + len(validation):
        raise TopicContractError('duplicate audit sample identities collide')
    pairs, counts = [], {}
    for scope, population in (('cross_split', product(development, validation)),
                              ('within_validation', combinations(validation, 2))):
        checked = 0
        for a, b in population:
            checked += 1
            left, right = cached[a['sample_id']], cached[b['sample_id']]
            reply = similarity(left[1], right[1], policy)
            if reply < policy['minimum_reply_jaccard']:
                continue
            context = similarity(left[0], right[0], policy)
            if context < policy['minimum_context_jaccard']:
                continue
            pairs.append({'scope': scope, 'left_sample_id': a['sample_id'], 'right_sample_id': b['sample_id'],
                'left_candidate_sha256': a['candidate_sha256'], 'right_candidate_sha256': b['candidate_sha256'],
                'context_similarity': context, 'reply_similarity': reply,
                'normalized_exact_pair': left[0][0] == right[0][0] and left[1][0] == right[1][0]})
        counts[scope] = checked
    return pairs, {'development_population': len(development), 'validation_population': len(validation),
        'pairs_checked': counts, 'cross_split_flagged_pairs': sum(r['scope'] == 'cross_split' for r in pairs),
        'within_validation_flagged_pairs': sum(r['scope'] == 'within_validation' for r in pairs),
        'flagged_validation_samples': len({r['right_sample_id'] for r in pairs} |
                                         {r['left_sample_id'] for r in pairs if r['scope'] == 'within_validation'}),
        'audit_completed_for_declared_scope': True, 'full_training_population_audited': False,
        'semantic_paraphrase_detection_claimed': False, 'p3_accepted': False, **GOVERNANCE}


def run_audit(*, packet: Path, development: Path, config_path: Path, output_root: Path,
              controlled_root: Path, execute: bool = False) -> dict:
    for path in (packet, development, output_root):
        private_path(path, controlled_root)
    policy = load_policy(config_path)
    if (file_digest(packet / 'manifest.json') != policy['packet_manifest_sha256']
            or file_digest(development / 'manifest.json') != policy['development_manifest_sha256']):
        raise TopicContractError('duplicate audit source identity changed')
    validation_manifest = verify_manifest(packet, {'validation.candidates.jsonl', 'identity.json'})
    _, train = verified_pilot(development)
    validation = list(rows(packet / 'validation.candidates.jsonl'))
    if [r['candidate_sha256'] for r in validation] != validation_manifest['identity']['candidate_digests']:
        raise TopicContractError('duplicate audit candidate population changed')
    for row in validation:
        validate_candidate(row)
    pairs, report = compare(train, validation, policy)
    inputs = {str(p): file_digest(p) for p in (config_path, packet / 'manifest.json', development / 'manifest.json')}
    sources = {name: file_digest(Path(__file__).with_name(name)) for name in
        ('topic_validation_duplicates.py', 'topic_validation_protocol.py', 'topic_validation.py',
         'topic_candidates.py', 'topic_adjudication.py', '_common.py')}
    identity = {'policy_sha256': digest(policy), 'inputs': inputs, 'source_digests': sources}
    output = output_root / ('topic-validation-duplicates_' + digest(identity)[:20])
    for parent in (packet, development):
        if output.resolve() == parent.resolve() or parent.resolve() in output.resolve().parents:
            raise TopicContractError('duplicate audit output overlaps source')
    result = {'status': 'planned', 'workspace': str(output), 'report': report}
    if not execute:
        return result
    if output.exists():
        manifest = verify_manifest(output, {'policy.json', 'report.json', 'pairs.jsonl'})
        if manifest['identity'] != identity or read_json(output / 'report.json') != report or list(rows(output / 'pairs.jsonl')) != pairs:
            raise TopicContractError('duplicate audit replay mismatch')
        return {**result, 'status': 'already_built'}
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_root.chmod(0o700)
    staging = Path(tempfile.mkdtemp(prefix='.duplicates-', dir=output_root))
    try:
        write_json(staging / 'policy.json', policy)
        write_json(staging / 'report.json', report)
        write_jsonl(staging / 'pairs.jsonl', pairs)
        manifest = {'identity': identity, **GOVERNANCE,
                    'output_digests': {p.name: file_digest(p) for p in staging.iterdir()}}
        manifest['manifest_sha256'] = digest(manifest)
        write_json(staging / 'manifest.json', manifest)
        if (any(file_digest(Path(p)) != sha for p, sha in inputs.items())
                or any(file_digest(Path(__file__).with_name(name)) != sha for name, sha in sources.items())):
            raise TopicContractError('duplicate audit sources changed during execution')
        _publish_directory_noreplace(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {**result, 'status': 'built'}
