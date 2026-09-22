"""Freeze a machine-reviewed pilot subset and a private review page.

This produces a train-only draft, not a training-ready snapshot or human review.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
import shutil
import tempfile

from ._common import digest, file_digest
from .datasets import _publish_directory_noreplace
from .topic_candidates import read_json, rows, private_path, write_json, write_jsonl
from .topic_context import TopicContractError


def export_review(pilot: Path, review: Path, output_root: Path, controlled_root: Path) -> dict:
    for path in (pilot,review,output_root):
        private_path(path,controlled_root)
    report=read_json(review/'report.json')
    decision_payload=read_json(review/'decisions.json')
    decisions=decision_payload['decisions']
    if report['status']!='complete' or digest(decisions)!=report['decisions_sha256']:
        raise TopicContractError('review is incomplete or changed')
    if decision_payload['identity']['pilot_manifest_sha256']!=file_digest(pilot/'manifest.json'):
        raise TopicContractError('review binds a different pilot')
    manifest=read_json(pilot/'manifest.json')
    if file_digest(pilot/'candidates.jsonl')!=manifest['output_digests']['candidates.jsonl']:
        raise TopicContractError('pilot candidate file changed')
    candidates=list(rows(pilot/'candidates.jsonl'))
    by_id={row['sample_id']:row for row in decisions}
    if len(by_id)!=len(decisions) or set(by_id)!={row['sample_id'] for row in candidates}:
        raise TopicContractError('review does not exactly cover pilot')
    for row in candidates:
        if by_id[row['sample_id']]['candidate_sha256']!=row['candidate_sha256']:
            raise TopicContractError('review candidate semantic digest mismatch')
    selected=[row for row in candidates if by_id[row['sample_id']]['status']=='keep']
    identity={'pilot_manifest_sha256':file_digest(pilot/'manifest.json'),
              'review_report_sha256':file_digest(review/'report.json'),
              'review_decisions_sha256':file_digest(review/'decisions.json')}
    output=output_root/('topic-reviewed-draft_'+digest(identity)[:20])
    if output.exists():
        existing=read_json(output/'manifest.json')
        if existing['identity']!=identity or any(file_digest(output/name)!=sha for name,sha in existing['output_digests'].items()):
            raise TopicContractError('existing export digest mismatch')
        return {'status':'already_built','output_dir':str(output),'kept_count':len(selected)}
    output_root.mkdir(parents=True,exist_ok=True,mode=0o700); output_root.chmod(0o700)
    staging=Path(tempfile.mkdtemp(prefix='.topic-reviewed-',dir=output_root))
    try:
        write_jsonl(staging/'train.draft.jsonl',selected)
        write_json(staging/'review-decisions.json',decision_payload)
        write_json(staging/'quality-report.json',report)
        parts=['<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width">',
               '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">',
               '<title>回复样本机器预审</title><style>body{max-width:1000px;margin:40px auto;padding:0 24px;background:#f6f5f1;font:16px/1.6 sans-serif;color:#222}article{background:#fff;margin:20px 0;padding:24px;border:1px solid #ddd}pre{white-space:pre-wrap;word-break:break-word}.keep{border-left:5px solid #287153}.reject{border-left:5px solid #a34937}.uncertain{border-left:5px solid #b18131}small{color:#666}</style>',
               f'<h1>回复样本机器预审</h1><p>共 {len(candidates)} 条；建议保留 {len(selected)} 条。机器判断用于试点诊断，尚未逐条人工审核。</p>']
        labels={'keep':'建议保留','reject':'排除','uncertain':'待定'}
        reasons={'usable_reply':'回复可用','insufficient_context':'上下文不足','off_context':'未回应上下文',
                 'missing_media':'依赖缺失媒体','low_signal':'沟通信号不足','privacy':'隐私风险',
                 'third_party':'第三方信息','identity_or_capability':'身份或能力边界',
                 'control_or_abuse':'控制或辱骂','unsafe':'不安全','uncertain':'判断不确定'}
        for row in candidates:
            decision=by_id[row['sample_id']]
            parts += [f'<article class="{decision["status"]}"><h2>{labels[decision["status"]]} · {html.escape(row["day"])}</h2>',
                      f'<p>{reasons[decision["reason"]]} · 机器置信度 {decision["confidence"]:.2f}</p>',
                      '<h3>模型实际输入</h3><pre>'+html.escape(row['messages'][1]['content'])+'</pre>',
                      '<h3>目标角色真实回复</h3><pre>'+html.escape(row['messages'][2]['content'])+'</pre>',
                      '<small>'+html.escape(row['sample_id'])+' · '+html.escape(row['candidate_sha256'])+'</small></article>']
        parts.append('</html>')
        with (staging/'review.html').open('x',encoding='utf-8') as handle:
            handle.write('\n'.join(parts))
        (staging/'review.html').chmod(0o600)
        result={'schema_version':'topic-reviewed-draft-v1','identity':identity,'counts':{'train':len(selected)},
                'machine_review_completed':True,'human_review_completed':False,'formal_training_eligible':False,
                'training_run':False,'promotable':False,'test_access':'not_loaded_by_pilot',
                'historical_test_status':'exposed_to_memory_processing',
                'next_requirements':['context_quality_gate','frozen_validation_population','versioned_training_admission','immutable_release','galatea_readiness'],
                'output_digests':{path.name:file_digest(path) for path in sorted(staging.iterdir())}}
        result['manifest_sha256']=digest(result)
        write_json(staging/'manifest.json',result)
        _publish_directory_noreplace(staging,output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {'status':'built','output_dir':str(output),'kept_count':len(selected),'training_ready':False}
