"""Recover invalid blind references with explicit allowed atomic speaker indices."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import fcntl
from pathlib import Path

import yaml

from . import topic_blind_reference as core
from . import topic_blind_reference_opus as opus
from . import topic_validation_recovery as recovery
from ._common import digest, file_digest
from .fact_review_server import _atomic_json
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json
from .topic_context import TopicContractError

METHOD = 'topic-blind-reference-index-recovery-v1'


def load_config(path: Path) -> dict:
    config = yaml.safe_load(path.read_text(encoding='utf-8'))
    expected = {**opus.load_config(path.parent / 'topic-blind-reference-opus-v1.yaml'),
        'schema_version': METHOD, 'inherited_references': 136, 'missing_references': 64,
        'max_repair_requests': 16,
        'budget': {'max_requests': 144, 'max_input_tokens': 3000000,
                   'max_total_output_tokens': 288000, 'max_output_tokens': 2000, 'request_timeout_seconds': 120},
        'preflight_budget': {'max_requests': 12, 'max_input_tokens': 180000,
                   'max_total_output_tokens': 24000, 'max_output_tokens': 2000, 'request_timeout_seconds': 120}}
    if config != expected:
        raise TopicContractError('invalid index recovery configuration')
    return config


def explicit_indices(case: dict) -> dict:
    result = deepcopy(case)
    result['selected']['case'].update(
        reply_speaker='target', responds_to_speaker_must_be='self',
        allowed_responds_to_indices=[m['index'] for m in case['selected']['case']['past_messages'] if m['speaker'] == 'self'],
        index_instruction='只抄录消息 index 字段。列表从0开始。target是回复的作者，不能作为对方回应锚点。'
                          'responds_to_indices必须是allowed_responds_to_indices的子集；'
                          '若只能关联到target自己的话，relation应为unknown且回应锚点为空。')
    return result


def fixtures() -> list:
    result = core.fixtures()
    examples = [
        ([('target', '我把桌子收拾好了。'), ('self', '那我给你泡杯热茶好吗？'),
          ('target', '等一下，我去洗手。'), ('self', '现在可以泡了吗？')], '可以了，谢谢。'),
        ([('self', '水果要苹果还是香蕉？'), ('target', '苹果。'), ('self', '要几个苹果？')], '两个就够了。')]
    for i, (past, reply) in enumerate(examples):
        case = {'past_messages': [{'index': j, 'speaker': role, 'text': text, 'kind': 'text', 'gap_before': False}
                                  for j, (role, text) in enumerate(past)], 'reply': reply}
        ids = [f'synthetic-index-{i}-{j}' for j in range(len(past))]
        result.append({'sample_id': f'synthetic-index-{i}', 'candidate_sha256': digest(case),
            'selected': {'source_ids': ids, 'case': case},
            'reference': {'status': 'available', 'prefix_complete_start': True, 'view': {'source_ids': ids, 'case': case}},
            'expected_context': 'sufficient'})
    return [explicit_indices(case) for case in result]


def replay_rows(workspace: Path, cases: list, config: dict, *, inherited: list | None = None) -> tuple[list, core.Journal]:
    identity = read_json(workspace / 'identity.json')
    manifest = verify_manifest(workspace, core.REQUIRED)
    if manifest['identity'] != identity:
        raise TopicContractError('reference parent identity mismatch')
    recovery.source_check(identity)
    journal = core.Journal(workspace, identity, config, '', Path('/nonexistent'), True)
    prior = {r['sample_id']: r for r in inherited or [] if r['selected'] and r['expanded']}
    output = []
    for case in cases:
        if case['sample_id'] in prior:
            output.append(prior[case['sample_id']])
            continue
        output.append(process_case(journal, case))
    if ({r['request_digest'] for r in read_json(workspace / 'budget.json')['attempts']} != journal.seen
            or read_json(workspace / 'references.json') != {'identity': identity, 'references': output}):
        raise TopicContractError('reference raw replay mismatch')
    return output, journal


def process_case(journal: core.Journal, case: dict) -> dict:
    first, error = journal.request(case, 'selected')
    second, second_error = (journal.request(case, 'expanded', first['label'])
        if first and case['reference']['status'] == 'available' else (None, 'first_or_evidence_unavailable'))
    return {'sample_id': case['sample_id'], 'candidate_sha256': case['candidate_sha256'],
        'selected': first, 'expanded': second, 'errors': {'selected': error, 'expanded': second_error},
        'selected_source_ids': case['selected']['source_ids'],
        'expanded_source_ids': case['reference'].get('view', {}).get('source_ids', []), **core.GOVERNANCE}


def run_reference(*, scope: str, config_path: Path, output_root: Path, controlled_root: Path,
                  base_url: str, auth_file: Path, authorization_reference: str,
                  packet: Path | None = None, consent: Path | None = None,
                  preflight: Path | None = None, parent: Path | None = None, execute: bool = False) -> dict:
    config = load_config(config_path)
    if scope not in {'preflight', 'private'} or not authorization_reference or not base_url.startswith('https://'):
        raise TopicContractError('invalid index recovery scope')
    for path in (output_root, packet, consent, preflight, parent):
        if path is not None:
            private_path(path, controlled_root)
    inputs = {str(p): file_digest(p) for p in (config_path,
        config_path.parent / 'topic-blind-reference-opus-v1.yaml',
        config_path.parent / 'topic-blind-reference-v1.yaml',
        config_path.parent.parent / 'scripts/recover_topic_blind_reference.py')}
    sources = {name: file_digest(Path(__file__).with_name(name)) for name in (
        Path(__file__).name, 'topic_blind_reference.py', 'topic_blind_reference_opus.py',
        'topic_validation_completion.py', 'topic_validation_budget.py', 'topic_review_budget.py',
        'topic_validation_review.py', 'topic_validation_recovery.py', 'topic_validation_protocol.py',
        'topic_adjudication.py', 'topic_candidates.py', 'fact_review_server.py', '_common.py')}
    inherited = []
    if scope == 'preflight':
        cases = fixtures()
        config = {**config, 'budget': config['preflight_budget'], 'max_repair_requests': 0}
    else:
        if any(p is None for p in (packet, consent, preflight, parent)):
            raise TopicContractError('index recovery requires all source bindings')
        route_manifest = verify_manifest(preflight, core.REQUIRED)
        route_identity = read_json(preflight / 'identity.json')
        recovery.source_check(route_identity)
        if (route_manifest['identity'] != route_identity or route_identity['method'] != METHOD
                or route_identity['scope'] != 'preflight'
                or route_identity['authorization_reference'] != authorization_reference
                or route_identity['base_url_sha256'] != digest(base_url.rstrip('/'))
                or not read_json(preflight / 'report.json')['route_gate_passed']):
            raise TopicContractError('index recovery route not qualified')
        packet_manifest, plain_cases = core.load_cases(packet, consent, config_path, base_url)
        source_identity = read_json(parent / 'identity.json')
        if (source_identity['method'] != opus.METHOD or source_identity['scope'] != 'private'
                or source_identity['authorization_reference'] == authorization_reference
                or source_identity['inputs'].get(str(packet / 'manifest.json')) != file_digest(packet / 'manifest.json')):
            raise TopicContractError('index recovery requires new scope and original private batch')
        old_config = opus.load_config(config_path.parent / 'topic-blind-reference-opus-v1.yaml')
        inherited, _ = replay_rows(parent, plain_cases, old_config)
        valid = [r for r in inherited if r['selected'] and r['expanded']]
        if (len(valid) != config['inherited_references'] or len(inherited) != config['population']
                or len(inherited) - len(valid) != config['missing_references']
                or any(r['selected'] or r['expanded'] for r in inherited if r not in valid)):
            raise TopicContractError('index recovery parent population changed')
        cases = [explicit_indices(c) for c in plain_cases]
        sources.update(packet_manifest['identity']['source_digests'])
        for path in (packet / 'manifest.json', consent, preflight / 'manifest.json', parent / 'manifest.json'):
            inputs[str(path)] = file_digest(path)
    prior = {r['sample_id']: r for r in inherited if r['selected'] and r['expanded']}
    identity = {'method': METHOD, 'scope': scope, 'model': config['model'], 'inputs': inputs,
        'source_digests': sources, 'budget': config['budget'], 'input_guard': config['input_guard'],
        'authorization_reference': authorization_reference, 'base_url_sha256': digest(base_url.rstrip('/')),
        'prompt_sha256': digest(core.PROMPT), 'schema_sha256': digest(core.SCHEMA),
        'candidate_digests': [c['candidate_sha256'] for c in cases], 'cases_sha256': digest(cases),
        'parent': str(parent) if parent else None, 'inherited_references_sha256': digest(inherited),
        'annotation_change': 'explicit_self_anchor_index_allowlist_no_text_or_rubric_change'}
    workspace = output_root / (METHOD + '-' + scope + '_' + digest(identity)[:20])
    plan = {'status': 'planned', 'workspace': str(workspace), 'scope': scope, 'population': len(cases),
        'inherited_references': len(prior), 'planned_first_requests': (len(cases) - len(prior)) * 2,
        'budget': config['budget'], 'max_repair_requests': config['max_repair_requests'],
        'model': config['model'], **core.GOVERNANCE}
    recovery.source_check(identity)
    if not execute:
        return plan
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace.chmod(0o700)
    with (workspace / 'run.lock').open('a') as lock:
        (workspace / 'run.lock').chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sealed = (workspace / 'manifest.json').exists()
        if sealed and verify_manifest(workspace, core.REQUIRED)['identity'] != identity:
            raise TopicContractError('index recovery sealed identity changed')
        if (workspace / 'identity.json').exists() and read_json(workspace / 'identity.json') != identity:
            raise TopicContractError('index recovery interrupted identity changed')
        if not sealed:
            _atomic_json(workspace / 'identity.json', identity)
            if not (workspace / 'budget.json').exists():
                _atomic_json(workspace / 'budget.json', {'identity_sha256': digest(identity), 'attempts': []})
        journal = core.Journal(workspace, identity, config, base_url, auth_file, sealed)
        pending = [c for c in cases if c['sample_id'] not in prior]
        if not sealed and read_json(workspace / 'budget.json')['attempts']:
            journal.dispatch_enabled = False
            for case in pending:
                process_case(journal, case)
            journal.dispatch_enabled = True
        with ThreadPoolExecutor(max_workers=1 if sealed or scope == 'preflight' else config['workers']) as pool:
            added = list(pool.map(lambda case: process_case(journal, case), pending))
        merged = {**prior, **{r['sample_id']: r for r in added}}
        references = [merged[c['sample_id']] for c in cases]
        ledger = read_json(workspace / 'budget.json')
        if (journal.seen != {r['request_digest'] for r in ledger['attempts']}
                or len(ledger['attempts']) > config['budget']['max_requests']
                or journal.repair_count > config['max_repair_requests']):
            raise TopicContractError('index recovery request population changed')
        completed = sum(bool(r['selected'] and r['expanded']) for r in references)
        calibrated = [bool(r['selected'] and r['expanded'] and all(r[s]['label']['context'] == c['expected_context']
            for s in ('selected', 'expanded'))) for c, r in zip(cases, references)] if scope == 'preflight' else []
        report = {**plan, 'status': 'complete' if completed == len(cases) else 'incomplete',
            'completed_references': completed, 'reference_review_completed': completed == len(cases),
            'valid_stage_judgments': sum(bool(r[s]) for r in references for s in ('selected', 'expanded')),
            'repair_requests': journal.repair_count, 'usage': journal.budget._usage(ledger),
            'response_failure_counts': dict(Counter(v['error'] for v in journal.results.values() if v['error'])),
            'circuit_reason': journal.halt or ledger.get('halt_reason'),
            'route_gate_passed': scope == 'preflight' and all(calibrated) and not journal.halt,
            'synthetic_cases_passed': sum(calibrated), 'inherited_valid_references_unchanged': True,
            'references_sha256': digest(references), 'machine_labels_disclosed_to_reference': False,
            'same_gateway_error_correlation_possible': True, 'same_family_as_claude_judge': True,
            'p3_accepted': False, 'provider_usage_bound_attested': False}
        recovery.source_check(identity)
        outputs = {'references.json': {'identity': identity, 'references': references}, 'report.json': report}
        if sealed:
            if any(read_json(workspace / n) != value for n, value in outputs.items()):
                raise TopicContractError('index recovery raw replay differs')
        else:
            for name, value in outputs.items():
                _atomic_json(workspace / name, value)
            names = core.REQUIRED | {p.name for p in workspace.glob('*.json') if len(p.stem) == 64}
            manifest = {'identity': identity, **core.GOVERNANCE,
                        'output_digests': {n: file_digest(workspace / n) for n in sorted(names)}}
            manifest['manifest_sha256'] = digest(manifest)
            _atomic_json(workspace / 'manifest.json', manifest)
        return report
