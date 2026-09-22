"""Offline, source-bound triage; never turn machine disagreement into approval."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import html
import json
from pathlib import Path
import shutil
import tempfile

import yaml

from ._common import digest, file_digest
from .consent import verify_consent
from .datasets import _publish_directory_noreplace
from .topic_candidates import private_path, read_json, rows, write_json, write_jsonl
from .topic_context import AtomicMessage, TopicContractError, merge_turns
from .topic_cross_review import load_config as load_cross_policy, load_inputs, summarize
from .topic_evidence_audit import evidence_view, parse_response, LABEL_PROPERTIES


METHOD = 'topic-disagreement-triage-v1'
QUEUES = {
    'prior_hard_risk_requires_adjudication': '旧风险待裁定',
    'reply_anchor_disagreement': '回应对象不同',
    'reply_link_disagreement': '关联判断不同',
    'context_sufficiency_disagreement': '上下文判断不同',
    'eligibility_disagreement': '保留资格不同',
    'both_nonkeep': '双方均未建议保留',
    'selected': '原机器共识参考组',
}
QUESTIONS = {
    'prior_hard_risk_requires_adjudication': '原风险针对谁、哪句话？是否涉及敏感内容？需要什么证据才能维持或撤销该风险？',
    'reply_anchor_disagreement': '两个模型各指向什么原话？是否确为多个回应对象，还是一方选错？同轮并不能证明等价。',
    'reply_link_disagreement': '回复是否确实回应 self？指出具体原消息，排除仅凭邻近或同话题推断。',
    'context_sufficiency_disagreement': '理解回复具体所指缺少什么？该证据已在输入、只在更早前文，还是来源也没有？',
    'eligibility_disagreement': '两方关联/完整性判断相同；分歧来自沟通价值、风险理由，还是置信度？',
    'both_nonkeep': '明确延期或排除的依据；未选前文是否真的包含必要证据？不得从答案补造问题。',
    'selected': '用于对照筛选标准的机器共识，仍不是人工真值。',
}


def load_policy(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding='utf-8'))
    governance = {'training_run': False, 'human_review_completed': False, 'formal_training_eligible': False,
                  'promotable': False, 'existing_labels_changed': False, 'selection_policy_changed': False,
                  'external_requests': 0}
    if (value['schema_version'] != 'topic-adjudication-policy-v1' or value['method'] != METHOD
            or value['governance'] != governance or value['allowed_splits'] != ['train']
            or value['minimum_confidence'] != .9 or value['max_candidates'] != 200
            or type(value['short_reply_max_characters']) is not int or not 1 <= value['short_reply_max_characters'] <= 16
            or value['queue_order'] != list(QUEUES)
            or {r['id'] for r in value['rules']} != {
                'independent_axes', 'short_reply', 'referent', 'topic_continuation', 'opener',
                'atomic_anchor', 'missing_history', 'risk', 'confidence', 'provenance'}):
        raise TopicContractError('invalid adjudication policy')
    return value


def verify_manifest(directory: Path, required: set[str]) -> dict:
    manifest = read_json(directory / 'manifest.json')
    outputs = manifest['output_digests']
    if (manifest['manifest_sha256'] != digest({k: v for k, v in manifest.items() if k != 'manifest_sha256'})
            or not required <= set(outputs)):
        raise TopicContractError('adjudication source manifest mismatch')
    for name, sha in outputs.items():
        path = directory / name
        if Path(name).name != name or path.is_symlink() or file_digest(path) != sha:
            raise TopicContractError('adjudication source artifact changed')
    return manifest


def load_fixtures(policy_path: Path, policy: dict) -> tuple[Path, list]:
    path = policy_path.parent / policy['synthetic_fixtures']
    if path.is_symlink() or policy_path.parent.resolve() not in path.resolve().parents:
        raise TopicContractError('synthetic fixtures must be within config directory')
    fixtures = json.loads(path.read_text(encoding='utf-8'))
    known = {r['id'] for r in policy['rules']}
    seen = set()
    for fixture in fixtures:
        if fixture['id'] in seen or fixture['rule'] not in known or not fixture['past']:
            raise TopicContractError('invalid synthetic fixture identity')
        seen.add(fixture['id'])
        messages = [{'index': i, 'speaker': m[0], 'text': m[1], 'kind': 'text', 'gap_before': False}
                    for i, m in enumerate(fixture['past'])]
        if any(m['speaker'] not in {'self', 'target'} for m in messages):
            raise TopicContractError('invalid synthetic fixture speaker')
        view = {'sample_id': fixture['id'], 'candidate_sha256': digest(fixture), 'kind': 'selected',
                'source_ids': [str(i) for i in range(len(messages))],
                'case': {'past_messages': messages, 'reply': fixture['reply']}}
        label = {'index': 0, 'confidence': .95, **fixture['expected']}
        parsed = parse_response({'output_text': json.dumps({'results': [label]})}, [view], .9)[0]
        if any(parsed[k] != v for k, v in fixture['expected'].items()):
            raise TopicContractError('synthetic reference violates evidence protocol')
    if not 12 <= len(fixtures) <= 30:
        raise TopicContractError('synthetic reference coverage outside frozen budget')
    return path, fixtures


def verified_cross(before: Path, after: Path, review: Path, audit: Path, cross: Path,
                   cross_policy_path: Path, controlled_root: Path) -> tuple[list, list, list, dict]:
    policy = load_cross_policy(cross_policy_path)
    old, current, previous, evidence, roster = load_inputs(before, after, review, audit, policy)
    manifest = verify_manifest(cross, {'identity.json', 'selection.json', 'report.json', 'decisions.json',
                                      'adjudication.jsonl', 'train.draft.jsonl', 'reply-evidence.jsonl'})
    identity = manifest['identity']
    for path, sha in identity['inputs'].items():
        private_path(Path(path), controlled_root)
        if file_digest(Path(path)) != sha:
            raise TopicContractError('cross-review input changed')
    if (identity['policy_sha256'] != digest(policy) or identity['roster_sha256'] != digest(roster)
            or manifest.get('human_review_completed') is not False
            or manifest.get('formal_training_eligible') is not False
            or manifest.get('training_run') is not False):
        raise TopicContractError('cross-review policy, population or provenance mismatch')
    record = read_json(cross / 'decisions.json')
    report = read_json(cross / 'report.json')
    decisions = record['decisions']
    expected_counts = {'v3': len(current), 'v2_changed': sum(r['changed'] for r in roster),
                       'v3_changed': sum(r['changed'] for r in roster)}
    if (record['identity'] != identity or report['status'] != 'complete'
            or digest(decisions) != report['decisions_sha256']
            or dict(Counter(r['scope'] for r in decisions)) != {k: v for k, v in expected_counts.items() if v}):
        raise TopicContractError('cross-review evidence is incomplete')
    for row in decisions:
        name = 'changed_reference' if row['scope'] == 'v3_changed' else 'independent'
        judge = policy['judges'][name]
        if (row['review_kind'] != 'machine' or row['judge'] != name
                or row['requested_family'] != judge['family'] or row['model_requested'] != judge['model']
                or not row['model_returned'].startswith(judge['returned_model_prefix'])):
            raise TopicContractError('cross-review judge binding mismatch')
    summary = summarize(old, current, previous, evidence, roster, decisions)
    cases = summary.pop('cases')
    bindings = summary.pop('bindings')
    if (any(report.get(key) != value for key, value in summary.items())
            or cases != list(rows(cross / 'adjudication.jsonl'))
            or bindings != list(rows(cross / 'reply-evidence.jsonl'))
            or [r for r in current if r['sample_id'] in set(summary['selected_sample_ids'])]
               != list(rows(cross / 'train.draft.jsonl'))):
        raise TopicContractError('cross-review selection cannot be reconstructed')
    return old, current, cases, {'manifest': manifest, 'roster': roster}


def full_prefixes(pilot: Path, candidates: list[dict]) -> dict:
    """Recover true turn membership before any selected-message gaps are removed."""
    by_target = {tuple(r['target_message_ids']): r for r in candidates}
    gap = read_json(pilot / 'policy.json')['context']['merge_gap_seconds']
    found = {}
    for bundle in rows(pilot / 'daily-bundles.jsonl'):
        if bundle.get('split') != 'train':
            raise TopicContractError('adjudication source bundle must be train-only')
        turns = merge_turns([AtomicMessage(**r) for r in bundle['messages']], gap)
        for index, turn in enumerate(turns):
            candidate = by_target.get(tuple(turn.ids))
            if candidate is None:
                continue
            sid = candidate['sample_id']
            messages = [asdict(m) for t in turns[:index] for m in t.messages]
            source = {m['message_id']: m for m in messages}
            if (sid in found or [asdict(m) for m in turn.messages] != candidate['target_messages']
                    or any(source.get(m['message_id']) != m for m in candidate['context_messages'])):
                raise TopicContractError('adjudication prefix disagrees with source candidate')
            unavailable = None
            try:
                evidence_view(candidate, messages, 'expanded_diagnostic')
            except TopicContractError as exc:
                if str(exc) not in {'diagnostic_privacy_hard_hit', 'diagnostic_canary_hit'}:
                    raise
                unavailable = str(exc)
            found[sid] = {'messages': messages if unavailable is None else [],
                          'source_ids': [m['message_id'] for m in messages], 'unavailable': unavailable,
                          'turn_by_message': {m.message_id: i for i, t in enumerate(turns[:index]) for m in t.messages}}
    if set(found) != {r['sample_id'] for r in candidates}:
        raise TopicContractError('adjudication target missing from prefix source')
    return found


def primary_queue(case: dict) -> str:
    original = case['disposition']
    if original in {'selected', 'prior_hard_risk_requires_adjudication', 'reply_anchor_disagreement'}:
        return original
    if original != 'cross_family_keep_not_agreed':
        raise TopicContractError('incomplete case cannot enter adjudication')
    reference, independent = case['reference_decision'], case['independent_decision']
    if reference['reply_link_correct'] != independent['reply_link_correct']:
        return 'reply_link_disagreement'
    if reference['context_complete'] != independent['context_complete']:
        return 'context_sufficiency_disagreement'
    if (reference['status'] == 'keep') != (independent['status'] == 'keep'):
        return 'eligibility_disagreement'
    return 'both_nonkeep'


def triage(candidate: dict, case: dict, prefix: dict, policy: dict, diagnostic: dict | None = None) -> dict:
    a, b = case['reference_decision'], case['independent_decision']
    anchors_a, anchors_b = set(a['responds_to_ids']), set(b['responds_to_ids'])
    shared = anchors_a & anchors_b
    membership = prefix['turn_by_message']
    if not (anchors_a | anchors_b) <= set(candidate['context_message_ids']):
        raise TopicContractError('triage anchor outside selected context')
    turn_overlap = {membership[mid] for mid in anchors_a} & {membership[mid] for mid in anchors_b}
    anchor_relation = ('shared_atomic_anchor' if shared else 'one_or_both_empty' if not anchors_a or not anchors_b
                       else 'same_source_turn_distinct_anchors' if turn_overlap else 'different_source_turns')
    selected = set(candidate['context_message_ids'])
    omitted = set(prefix['source_ids']) - selected
    diagnostic_hint = None
    if diagnostic is not None:
        missing = sorted(set(diagnostic['required_context_ids']) - selected)
        diagnostic_hint = {'method': diagnostic['method'], 'review_kind': 'machine',
                           'decision_sha256': digest(diagnostic), 'status': diagnostic['status'],
                           'required_ids_missing_from_current_input': missing,
                           'is_proof_of_true_omission': False}
    return {'sample_id': candidate['sample_id'], 'candidate_sha256': candidate['candidate_sha256'],
            'case_sha256': digest(case), 'queue': primary_queue(case),
            'original_disposition': case['disposition'], 'prior_hard_risks': case['prior_hard_risks'],
            'anchor_relation': anchor_relation, 'shared_anchor_ids': sorted(shared),
            'source_prefix_message_count': len(prefix['source_ids']), 'selected_message_count': len(selected),
            'omitted_message_count': len(omitted), 'all_available_past_selected': not omitted,
            'expanded_view_unavailable': prefix['unavailable'], 'prior_expanded_diagnostic': diagnostic_hint,
            'short_reply': len(candidate['messages'][-1]['content'].strip()) <= policy['short_reply_max_characters'],
            'reply_length_is_rejection_rule': False, 'same_turn_is_approval_rule': False,
            'diagnostic_evidence_used_for_context_selection': False,
            'adjudication_status': 'pending' if primary_queue(case) != 'selected' else 'machine_reference_only',
            'human_review_completed': False, 'formal_training_eligible': False}


def diagnostic_bindings(old: list[dict], pilot: Path, audit: Path, roster: list[dict]) -> dict:
    expanded = [r for r in read_json(audit / 'decisions.json')['decisions'] if r['view_kind'] == 'expanded_diagnostic']
    old_by_id = {r['sample_id']: r for r in old}
    prefixes = full_prefixes(pilot, [old_by_id[r['sample_id']] for r in expanded])
    after_by_old = {r['before_sample_id']: r['after_sample_id'] for r in roster}
    result = {}
    for decision in expanded:
        sid = decision['sample_id']
        prefix = prefixes[sid]
        if prefix['unavailable']:
            continue
        view = evidence_view(old_by_id[sid], prefix['messages'], 'expanded_diagnostic')
        label = {'index': 0, **{key: decision[key] for key in LABEL_PROPERTIES if key != 'index'}}
        parsed = parse_response({'output_text': json.dumps({'results': [label]})}, [view], .9)[0]
        if parsed != decision:
            raise TopicContractError('expanded diagnostic binding changed')
        result[after_by_old[sid]] = decision
    return result


def render_page(candidates: list[dict], cases: list[dict], triaged: list[dict], prefixes: dict, policy: dict) -> str:
    by_id = {r['sample_id']: r for r in candidates}
    opinions = {r['sample_id']: r for r in cases}
    esc = lambda value: html.escape(str(value), quote=True)
    parts = ['<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'unsafe-inline\'">',
             '<title>回复证据裁定工作台</title><style>body{max-width:1120px;margin:32px auto;padding:0 20px;font:16px/1.7 sans-serif;color:#222}header{position:sticky;top:0;background:#fff;border-bottom:1px solid #bbb;padding:12px 0}article{border:1px solid #bbb;margin:24px 0;padding:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere}table{width:100%;border-collapse:collapse}td,th{padding:8px;border:1px solid #ddd;text-align:left}blockquote{margin:8px 0;padding:8px 16px;border-left:4px solid #396}li.omitted{border-left:3px solid #c75;padding-left:8px}li a{font-size:13px}select{font:inherit}small{color:#555}</style>',
             '<h1>回复证据裁定工作台</h1><p>机器意见和来源证据。待裁定不等于排除，机器共识不等于人工通过。本页只读，不提交决定。</p>',
             '<details><summary>固定判据</summary><ol>' + ''.join('<li>' + esc(r['text']) + '</li>' for r in policy['rules']) + '</ol></details>',
             '<header><label>核验队列 <select id="queue"><option value="pending">全部待裁定</option><option value="all">全部含参考组</option>' +
             ''.join('<option value="' + key + '">' + esc(label) + '</option>' for key, label in QUEUES.items()) +
             '</select></label>　<span id="count"></span></header>']
    for item in triaged:
        row, case = by_id[item['sample_id']], opinions[item['sample_id']]
        prefix = prefixes[item['sample_id']]
        source = {r['message_id']: r for r in row['context_messages']}
        ordinal = {r['message_id']: i + 1 for i, r in enumerate(prefix['messages'])}
        article_id = row['sample_id']
        parts += ['<article data-queue="' + item['queue'] + '"><h2>' + esc(QUEUES[item['queue']]) + '</h2>',
                  '<p>' + esc(QUESTIONS[item['queue']]) + '</p>',
                  '<small>样本 ' + esc(article_id) + ' · ' + esc(row['day']) + '</small>',
                  '<h3>模型实际输入</h3><pre>' + esc(row['messages'][1]['content']) + '</pre>',
                  '<h3>真实目标回复</h3><blockquote>' + esc(row['messages'][-1]['content']) + '</blockquote>',
                  '<table><tr><th>审核器</th><th>状态 / 理由 / 置信度</th><th>关联 / 完整</th></tr>']
        for name, decision in [('GPT', case['reference_decision']), ('Claude', case['independent_decision'])]:
            parts.append('<tr><td>' + name + '</td><td>' + esc(f"{decision['status']} / {decision['reason']} / {decision['confidence']}") +
                         '</td><td>' + esc(f"{decision['reply_link_correct']} / {decision['context_complete']}") + '</td></tr>')
        parts.append('</table>')
        for name, decision in [('GPT', case['reference_decision']), ('Claude', case['independent_decision'])]:
            parts.append('<h3>' + name + ' 所指的回应对象</h3>')
            for mid in decision['responds_to_ids']:
                parts.append('<blockquote>前文 #' + str(ordinal.get(mid, '?')) + '：' + esc(source[mid]['content']) + '</blockquote>')
            if not decision['responds_to_ids']:
                parts.append('<p>未提供回应对象。</p>')
        parts.append('<details><summary>来源边界与分流依据</summary><pre>' + esc(json.dumps(item, ensure_ascii=False, indent=2)) + '</pre></details>')
        parts.append('<details><summary>完整同 session/day 过去视图（只作诊断；不进入训练输入）</summary>')
        if prefix['unavailable']:
            parts.append('<p>扩展内容未通过隐私/金丝雀检查，已隐藏。</p>')
        else:
            parts.append('<ol>')
            for message in prefix['messages']:
                selected = message['message_id'] in source
                a = message['message_id'] in case['reference_decision']['required_context_ids']
                b = message['message_id'] in case['independent_decision']['required_context_ids']
                flags = ('输入内' if selected else '未选入') + (' · GPT 必要证据' if a else '') + (' · Claude 必要证据' if b else '')
                parts.append('<li class="' + ('' if selected else 'omitted') + '"><small>' + esc(message['role'] + ' · ' + flags) +
                             '</small><pre>' + esc(message['content']) + '</pre></li>')
            parts.append('</ol>')
        parts.append('</details></article>')
    parts.append("<script>const q=document.getElementById('queue');function filter(){let n=0;document.querySelectorAll('article').forEach(e=>{const k=e.dataset.queue;const show=q.value==='all'||(q.value==='pending'?k!=='selected':k===q.value);e.hidden=!show;if(show)n++});document.getElementById('count').textContent=n+' 条'}q.addEventListener('change',filter);filter()</script></html>")
    return '\n'.join(parts)


def build_adjudication(*, before: Path, after: Path, review: Path, audit: Path, cross: Path,
                       consent: Path, policy_path: Path, cross_policy_path: Path,
                       output_root: Path, controlled_root: Path, execute: bool = False) -> dict:
    for path in (before, after, review, audit, cross, consent, output_root):
        private_path(path, controlled_root)
    if any(output_root == path or path in output_root.parents for path in (before, after, review, audit, cross)):
        raise TopicContractError('adjudication output overlaps inputs')
    policy = load_policy(policy_path)
    fixture_path, fixtures = load_fixtures(policy_path, policy)
    authorization = verify_consent(consent, required_purposes={'processing', 'persona_style', 'evaluation'}, required_message_types={'text'})
    if any(read_json(p / 'source-audit.json')['consent_file_sha256'] != authorization['consent_file_sha256'] for p in (before, after)):
        raise TopicContractError('adjudication consent mismatch')
    old, candidates, cases, bound = verified_cross(before, after, review, audit, cross, cross_policy_path, controlled_root)
    if len(candidates) > policy['max_candidates']:
        raise TopicContractError('adjudication candidate budget exceeded')
    prefixes = full_prefixes(after, candidates)
    diagnostics = diagnostic_bindings(old, before, audit, bound['roster'])
    by_id = {r['sample_id']: r for r in cases}
    triaged = [triage(r, by_id[r['sample_id']], prefixes[r['sample_id']], policy, diagnostics.get(r['sample_id'])) for r in candidates]
    triaged.sort(key=lambda row: (policy['queue_order'].index(row['queue']), row['sample_id']))
    pending = [r for r in triaged if r['queue'] != 'selected']
    identity = {'method': METHOD, 'policy_sha256': digest(policy), 'cross_manifest_sha256': file_digest(cross / 'manifest.json'),
                'inputs': {str(path): file_digest(path) for path in (before / 'manifest.json', after / 'manifest.json',
                           review / 'decisions.json', audit / 'manifest.json', cross / 'manifest.json', consent,
                           policy_path, cross_policy_path, fixture_path)},
                'implementation_sha256': file_digest(Path(__file__)),
                'dependencies': {name: file_digest(Path(__file__).with_name(name)) for name in (
                    'topic_candidates.py', 'topic_context.py', 'topic_cross_review.py', 'topic_evidence_audit.py')}}
    output = output_root / ('topic-adjudication_' + digest(identity)[:20])
    report = {'method': METHOD, 'population': len(candidates), 'pending_count': len(pending),
              'queue_counts': dict(Counter(r['queue'] for r in triaged)),
              'anchor_disagreement_types': dict(Counter(r['anchor_relation'] for r in triaged if r['queue'] == 'reply_anchor_disagreement')),
              'pending_history_counts': dict(Counter('all_past_selected' if r['all_available_past_selected'] else 'unselected_past_exists' for r in pending)),
              'pending_short_reply_count': sum(r['short_reply'] for r in pending),
              'prior_diagnostic_count': len(diagnostics), 'omitted_history_is_not_omission_proof': True,
              'expanded_unavailable_count': sum(bool(r['expanded_view_unavailable']) for r in triaged),
              'new_adjudications': 0, 'validation_review_started': False, 'quality_gate_passed': False,
              'synthetic_fixture_count': len(fixtures), 'judges_calibrated_on_fixtures': False,
              'rubric_is_human_ground_truth': False, **policy['governance']}
    result = {'status': 'planned', 'output_dir': str(output), 'report': report}
    if not execute:
        return result
    if output.exists():
        existing = verify_manifest(output, {'policy.json', 'report.json', 'triage.jsonl', 'review.html', 'calibration-fixtures.json'})
        if existing['identity'] != identity or read_json(output / 'report.json') != report:
            raise TopicContractError('existing adjudication identity changed')
        return {**result, 'status': 'already_built'}
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_root.chmod(0o700)
    staging = Path(tempfile.mkdtemp(prefix='.topic-adjudication-', dir=output_root))
    try:
        write_json(staging / 'policy.json', policy)
        write_json(staging / 'calibration-fixtures.json', {'origin': 'synthetic_specification_not_human_audit', 'fixtures': fixtures})
        write_json(staging / 'report.json', report)
        write_jsonl(staging / 'triage.jsonl', triaged)
        path = staging / 'review.html'
        path.write_text(render_page(candidates, cases, triaged, prefixes, policy), encoding='utf-8')
        path.chmod(0o600)
        manifest = {'schema_version': 'topic-adjudication-manifest-v1', 'identity': identity, **policy['governance'],
                    'output_digests': {p.name: file_digest(p) for p in staging.iterdir()}}
        manifest['manifest_sha256'] = digest(manifest)
        write_json(staging / 'manifest.json', manifest)
        if any(file_digest(Path(p)) != sha for p, sha in identity['inputs'].items()):
            raise TopicContractError('adjudication input changed during build')
        _publish_directory_noreplace(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {**result, 'status': 'built'}
