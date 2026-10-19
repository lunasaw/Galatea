"""Causal reply candidates and immutable, train-only daily-topic pilot artifacts."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

import jsonschema
import yaml

from ._common import canonical_json, digest, file_digest
from .canary import DEFAULT_PATTERN
from .consent import verify_consent
from .datasets import _publish_directory_noreplace
from .redact import scan_adjacent_messages
from .reply_links import build_reply_link, validate_references
from .topic_context import (
    AtomicMessage, POLICY_VERSIONS, TopicContractError, Turn, categories, merge_turns, select_context,
    serialize_history,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PILOT_VERSION = "daily-topic-pilot-v1"
FIELDS = ("message_id", "timestamp", "source_record_index")
PATTERNS = {key: re.compile(r'"' + key + r'"\s*:\s*("(?:\\.|[^"\\])*"|\d+|null)') for key in FIELDS}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


def load_policy(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "daily-topic-sft-config-v1":
        raise TopicContractError("unsupported topic config")
    if value.get("allowed_splits") != ["train"] or value.get("target_role") != "target":
        raise TopicContractError("pilot requires train-only target replies")
    if value["context"]["cross_session"] or value["context"]["cross_day"]:
        raise TopicContractError("cross-session/day contexts are not enabled")
    if value["context"]["policy_version"] not in POLICY_VERSIONS:
        raise TopicContractError("unknown context policy")
    if not 1 <= value["context"]["max_context_turns"] <= value["context"]["max_history_turns"] <= 64:
        raise TopicContractError("invalid bounded context limits")
    if value["governance"]["training_run"] or value["governance"]["formal_training_eligible"]:
        raise TopicContractError("pilot must remain non-training data preparation")
    if not 1 <= value["selection"]["max_days"] <= 50 or not 1 <= value["selection"]["max_candidates"] <= 200:
        raise TopicContractError("pilot exceeds bounded day/candidate budget")
    if not 0 < value["encoding"]["target_token_reserve"] < value["encoding"]["max_length"]:
        raise TopicContractError("invalid fixed target reserve")
    return value


def private_path(path: Path, root: Path) -> Path:
    absolute = path.absolute()
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise TopicContractError("private path contains symlink")
    resolved = absolute.resolve()
    if resolved == root.resolve() or root.resolve() not in resolved.parents:
        raise TopicContractError("path outside controlled root")
    return resolved


def source_identity(source: Path, memory: Path, consent: Path, config: dict) -> tuple[dict, dict, dict, dict]:
    manifest_path = source / "manifests/source_manifest.json"
    split_path = source / "manifests/split_manifest.json"
    source_manifest = read_json(manifest_path)
    split = read_json(split_path)
    if source_manifest["manifest_sha256"] != digest({k:v for k,v in source_manifest.items() if k != "manifest_sha256"}):
        raise TopicContractError("source manifest digest mismatch")
    mapping = split["session_ids_by_split"]
    if split["split_sha256"] != digest(mapping) or source_manifest["split_sha256"] != split["split_sha256"]:
        raise TopicContractError("split digest mismatch")
    split_digest = digest({k:v for k,v in split.items() if k not in {"split_sha256", "manifest_sha256"}})
    if split["manifest_sha256"] != split_digest:
        raise TopicContractError("split manifest digest mismatch")
    session_map = {sid: part for part, ids in mapping.items() for sid in ids}
    if len(session_map) != sum(len(ids) for ids in mapping.values()):
        raise TopicContractError("cross-split session identity")
    authorization = verify_consent(consent, required_purposes=config["governance"]["required_purposes"], required_message_types={"text"})
    if authorization["consent_digest"] != source_manifest["consent_digest"]:
        raise TopicContractError("source consent binding mismatch")
    universe: dict[str, tuple[str, str]] = {}
    for row in rows(source / "manifests/lineage.jsonl"):
        if row["stage"] != "session":
            continue
        sid = row["object_id"]
        if sid != "session_" + digest(row["source_message_ids"])[:16]:
            raise TopicContractError("session message identity mismatch")
        for mid in row["source_message_ids"]:
            if mid in universe:
                raise TopicContractError("message assigned to multiple sessions")
            universe[mid] = (sid, session_map[sid])
    if {value[0] for value in universe.values()} != set(session_map):
        raise TopicContractError("lineage does not cover frozen sessions")
    memory_manifest = read_json(memory / "manifest.json")
    if memory_manifest["manifest_sha256"] != digest({k:v for k,v in memory_manifest.items() if k != "manifest_sha256"}):
        raise TopicContractError("memory manifest digest mismatch")
    if memory_manifest["source_manifest_sha256"] != source_manifest["manifest_sha256"]:
        raise TopicContractError("memory/source identity mismatch")
    exposure_messages = Counter()
    exposure_sessions = defaultdict(set)
    chunk_counts = Counter()
    for name, relative in (("evidence_map", "manifests/evidence-map.jsonl"), ("chunk_manifest", "manifests/chunks.jsonl")):
        if file_digest(memory / relative) != memory_manifest["output_digests"][name]:
            raise TopicContractError("memory audit file digest mismatch")
        for row in rows(memory / relative):
            if name == "evidence_map":
                sid, part = universe[row["message_id"]]
                if sid != row["session_id"]:
                    raise TopicContractError("memory evidence session mismatch")
                exposure_messages[part] += 1
                exposure_sessions[part].add(sid)
            else:
                chunk_counts[session_map[row["session_id"]]] += 1
    audit = {
        "schema_version": "topic-source-audit-v1",
        "source_manifest_sha256": source_manifest["manifest_sha256"],
        "split_sha256": split["split_sha256"],
        "consent_file_sha256": authorization["consent_file_sha256"],
        "verified_purposes": authorization["purposes"],
        "source_session_counts": split["session_counts"],
        "source_candidate_counts": split["candidate_counts"],
        "historical_memory_messages_by_split": dict(exposure_messages),
        "historical_memory_sessions_by_split": {key:len(value) for key,value in exposure_sessions.items()},
        "historical_memory_chunks_by_split": dict(chunk_counts),
        "historical_test_status": "exposed_to_memory_processing" if exposure_messages["test"] else "not_proven_untouched",
        "final_test_ready": False,
        "required_final_test_action": "new_unexposed_temporal_or_independent_holdout",
        "test_body_materialized_by_pilot": False,
        "owner_scope": memory_manifest["identity"]["owner_scope"],
    }
    return source_manifest, universe, audit, authorization


def load_train_days(source: Path, universe: dict, owner: str, config: dict) -> tuple[dict, list[str], dict]:
    """Inspect IDs/dates first; JSON-decode bodies only for selected train days."""
    metadata = {}
    days = set()
    path = source / "redacted/messages.jsonl"
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle):
            values = {}
            for field, pattern in PATTERNS.items():
                matches = pattern.findall(line)
                if len(matches) != 1:
                    raise TopicContractError("normalized message metadata is ambiguous")
                values[field] = json.loads(matches[0])
            mid = values["message_id"]
            if mid not in universe or universe[mid][1] != "train":
                continue
            if not values["timestamp"] or type(values["source_record_index"]) is not int:
                raise TopicContractError("source order or timestamp missing")
            parsed = datetime.fromisoformat(values["timestamp"])
            if parsed.tzinfo is None:
                raise TopicContractError("timestamp lacks timezone")
            day = parsed.astimezone(ZoneInfo(config["timezone"])).date().isoformat()
            if mid in metadata:
                raise TopicContractError("duplicate normalized message")
            metadata[mid] = (line_number, day)
            days.add(day)
    selected_days = sorted(sorted(days, key=lambda day: digest({"day": day, "seed": config["selection"]["seed"]}))[:config["selection"]["max_days"]])
    selected = {number: (mid, day) for mid,(number,day) in metadata.items() if day in selected_days}
    grouped = defaultdict(list)
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle):
            if number not in selected:
                continue
            row = json.loads(line)
            mid, day = selected[number]
            sid, part = universe[mid]
            grouped[(sid, day)].append(AtomicMessage(
                mid, sid, owner, part,
                datetime.fromisoformat(row["timestamp"]).astimezone(timezone.utc).isoformat(),
                row["source_record_index"], day, row["speaker_role"], row["text_redacted"] or "",
                row["message_kind"], row.get("reply_to"),
            ))
    for key in grouped:
        grouped[key].sort(key=lambda row: (row.timestamp, row.order))
    return dict(grouped), selected_days, {"train_days_available":len(days), "selected_train_messages":len(selected), "selected_session_day_slices":len(grouped)}


def encode_candidate(messages: list[dict], tokenizer: Any, maximum: int, reserve: int) -> dict:
    prompt_text = tokenizer.apply_chat_template(messages[:-1], tokenize=False, add_generation_prompt=True)
    full_text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
    prompt = tokenizer(prompt_text, truncation=False)["input_ids"]
    full = tokenizer(full_text, truncation=False)["input_ids"]
    if full[:len(prompt)] != prompt:
        raise TopicContractError("template_prompt_not_prefix")
    target_count = len(full) - len(prompt)
    if target_count <= 0 or target_count > reserve or len(full) > maximum:
        raise TopicContractError("target_or_sequence_over_budget")
    return {"prompt_tokens": len(prompt), "target_tokens": target_count,
            "serialized_tokens": len(full), "target_start": len(prompt), "target_end": len(full),
            "eos_policy": "supervise_terminal_template_suffix", "truncation": "none"}


def build_candidate(prefix: list[Turn], target: Turn, tokenizer: Any, config: dict, *, arm: str = "topic",
                    prefix_complete_start: bool = False) -> dict:
    if {mid for turn in prefix for mid in turn.ids}.intersection(target.ids):
        raise TopicContractError("target_in_context")
    encoding = config["encoding"]
    chosen, topic = select_context(prefix, tokenizer, system=encoding["system_prompt"],
        max_length=encoding["max_length"], target_reserve=encoding["target_token_reserve"],
        max_turns=config["context"]["max_context_turns"], arm=arm,
        policy_version=config["context"]["policy_version"], prefix_complete_start=prefix_complete_start)
    source = [row for turn in chosen for row in turn.messages]
    targets = list(target.messages)
    if set(row.message_id for row in source) & set(target.ids):
        raise TopicContractError("target_in_context")
    if any((row.timestamp,row.order) >= (targets[0].timestamp,targets[0].order) for row in source):
        raise TopicContractError("noncausal_context")
    if any((row.session_id,row.day,row.split,row.owner_scope) != (
        targets[0].session_id,targets[0].day,targets[0].split,targets[0].owner_scope
    ) for row in source):
        raise TopicContractError("context_scope_mismatch")
    if any(row.role != "target" for row in targets):
        raise TopicContractError("wrong_target_role")
    if any(row.role not in {"self","target"} or row.kind != "text" or not row.content.strip()
           or "<MEDIA_" in row.content for row in source + targets):
        raise TopicContractError("unsupported_or_missing_message_content")
    link = build_reply_link(prefix, target, chosen)
    messages = [{"role":"system","content":encoding["system_prompt"]},
                {"role":"user","content":serialize_history(chosen)},
                {"role":"assistant","content":target.content}]
    token_stats = encode_candidate(messages, tokenizer, encoding["max_length"], encoding["target_token_reserve"])
    privacy_messages = [{"content":row.content,"source_record_index":row.order} for row in source + targets]
    if scan_adjacent_messages(privacy_messages)["hard_leak_count"]:
        raise TopicContractError("privacy_hard_hit")
    if any(re.search(DEFAULT_PATTERN,row.content) for row in source + targets):
        raise TopicContractError("canary_hit")
    result = {
        "schema_version":"topic-reply-candidate-v1", "owner_scope":targets[0].owner_scope,
        "split":targets[0].split, "day":targets[0].day, "session_id":targets[0].session_id,
        "sample_id":"topicreply_" + digest({"target":target.ids,"arm":arm,"policy":digest(config)})[:24],
        "policy_sha256":digest(config), "messages":messages,
        "context_messages":[asdict(row) for row in source],
        "target_messages":[asdict(row) for row in targets],
        "context_message_ids":[row.message_id for row in source], "target_message_ids":target.ids,
        "cutoff":{"timestamp":targets[0].timestamp,"source_record_index":targets[0].order},
        "topic":topic, "reply_link":link, "tokens":token_stats,
        "review_status":"uncertain", "human_review_completed":False,
        "formal_training_eligible":False,
    }
    result["candidate_sha256"] = digest(result)
    return result


def tokenizer_identity(model_path: Path, revision: str) -> dict:
    names = ["tokenizer.json", "tokenizer_config.json", "chat_template.jinja", "vocab.json", "merges.txt"]
    hashes = {}
    for name in names:
        path = model_path / name
        metadata = model_path / ".cache/huggingface/download" / (name + ".metadata")
        if not metadata.is_file() or metadata.read_text().splitlines()[0] != revision:
            raise TopicContractError("tokenizer files are not bound to the requested revision")
        hashes[name] = file_digest(path)
    return {"revision":revision,"file_digests":hashes,"files_sha256":digest(hashes)}


def write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(canonical_json(value) + "\n")
    path.chmod(0o600)


def write_jsonl(path: Path, values: list[dict]) -> None:
    with path.open("x",encoding="utf-8") as handle:
        for value in values:
            handle.write(canonical_json(value) + "\n")
    path.chmod(0o600)


def write_review_page(path: Path, candidates: list[dict]) -> None:
    parts = ['<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">',
             '<title>真实回复样本试点</title><style>body{max-width:980px;margin:40px auto;padding:0 20px;font:16px/1.6 sans-serif;background:#f6f5f1;color:#222}article{background:white;padding:24px;margin:20px 0;border:1px solid #ddd}pre{white-space:pre-wrap;word-break:break-word}small{color:#666}</style>',
             '<h1>真实回复样本试点</h1><p>只展示 train。上下文及目标均为脱敏原话；候选尚待质量核验。</p>']
    for index, row in enumerate(candidates,1):
        parts.append(f'<article><h2>{index}. {html.escape(row["day"])} · {html.escape(row["topic"]["topic_category"])}</h2>')
        parts.append(f'<small>{html.escape(row["sample_id"])} · {row["tokens"]["serialized_tokens"]} tokens</small>')
        parts.append('<h3>模型看到的上下文</h3><pre>' + html.escape(row["messages"][1]["content"]) + '</pre>')
        parts.append('<h3>目标角色真实回复</h3><pre>' + html.escape(row["messages"][2]["content"]) + '</pre>')
        parts.append('<details><summary>来源与关联</summary><pre>' + html.escape(json.dumps({
            "context_message_ids":row["context_message_ids"],"target_message_ids":row["target_message_ids"],
            "reply_link":row["reply_link"],"candidate_sha256":row["candidate_sha256"],
        },ensure_ascii=False,indent=2)) + '</pre></details></article>')
    parts.append('</html>')
    with path.open('x',encoding='utf-8') as handle:
        handle.write('\n'.join(parts))
    path.chmod(0o600)


def verified_pilot(path: Path) -> tuple[dict, list[dict]]:
    manifest = read_json(path / 'manifest.json')
    if manifest['manifest_sha256'] != digest({k: v for k, v in manifest.items() if k != 'manifest_sha256'}):
        raise TopicContractError('pilot manifest digest mismatch')
    for name, sha in manifest['output_digests'].items():
        if Path(name).name != name or file_digest(path / name) != sha:
            raise TopicContractError('pilot output digest mismatch')
    candidates = list(rows(path / 'candidates.jsonl'))
    if len({tuple(row['target_message_ids']) for row in candidates}) != len(candidates):
        raise TopicContractError('duplicate target population')
    for row in candidates:
        if row['split'] != 'train' or row['candidate_sha256'] != digest({k: v for k, v in row.items() if k != 'candidate_sha256'}):
            raise TopicContractError('invalid reference candidate')
    return manifest, candidates


def freeze_target_attempts(attempts: list, reference_rows: list[dict]) -> list:
    """Match the exact previous target population, without replacing failures."""
    by_target = {tuple(target.ids): (key, prefix, target) for key, prefix, target in attempts}
    frozen = []
    for row in reference_rows:
        attempt = by_target.get(tuple(row['target_message_ids']))
        if attempt is None or [asdict(message) for message in attempt[2].messages] != row['target_messages']:
            raise TopicContractError('frozen target missing or changed')
        frozen.append(attempt)
    return frozen


def build_pilot(*, source: Path, memory: Path, consent: Path, config_path: Path,
                tokenizer_path: Path, output_root: Path, controlled_root: Path,
                execute: bool = False, reference_pilot: Path | None = None) -> dict:
    for path in (source,memory,consent,output_root):
        private_path(path,controlled_root)
    if source in output_root.parents or memory in output_root.parents or output_root in source.parents:
        raise TopicContractError("output must be separate from source")
    config = load_policy(config_path)
    source_manifest, universe, audit, authorization = source_identity(source,memory,consent,config)
    grouped, days, coverage = load_train_days(source,universe,audit["owner_scope"],config)
    tokenizer_info = tokenizer_identity(tokenizer_path,config["encoding"]["tokenizer_revision"])
    reference_rows = None
    if reference_pilot is not None:
        private_path(reference_pilot, controlled_root)
        reference_manifest, reference_rows = verified_pilot(reference_pilot)
        previous = read_json(reference_pilot / 'policy.json')
        identity = reference_manifest['identity']
        if (identity['source_manifest_sha256'] != source_manifest['manifest_sha256']
                or identity['selected_days'] != days or identity['tokenizer'] != tokenizer_info
                or any(previous[key] != config[key] for key in (
                    'selection', 'encoding', 'timezone', 'allowed_splits', 'target_role'))
                or any(previous['context'][key] != config['context'][key] for key in (
                    'merge_gap_seconds', 'cross_session', 'cross_day', 'max_history_turns'))):
            raise TopicContractError('reference pilot population/protocol mismatch')
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path,local_files_only=True)
    inputs = {str(path):file_digest(path) for path in (
        config_path,source/'manifests/source_manifest.json',source/'manifests/split_manifest.json',
        source/'manifests/lineage.jsonl',source/'redacted/messages.jsonl',consent,
        memory/'manifest.json',memory/'manifests/evidence-map.jsonl',memory/'manifests/chunks.jsonl',
    )}
    if reference_pilot is not None:
        inputs[str(reference_pilot / 'manifest.json')] = file_digest(reference_pilot / 'manifest.json')
    implementation = {name:file_digest(Path(__file__).with_name(name)) for name in (
        'topic_context.py','reply_links.py','topic_candidates.py','training.py')}
    implementation['candidate_schema'] = file_digest(PROJECT_ROOT/'schemas/topic-reply-candidate.schema.json')
    universe_stats = Counter()
    attempts = []
    for (sid,day), messages in sorted(grouped.items()):
        try:
            validate_references(messages,universe)
            turns = merge_turns(messages,config['context']['merge_gap_seconds'])
        except TopicContractError as exc:
            universe_stats[str(exc)] += 1
            continue
        for index, turn in enumerate(turns):
            if turn.role == 'target':
                # Consecutive target turns separated by a topic boundary need
                # independent alignment; do not silently attach them to self.
                if index == 0 or turns[index-1].role != 'self':
                    universe_stats['no_incoming_self_turn'] += 1
                    continue
                start = max(0,index-config['context']['max_history_turns'])
                attempts.append((digest({'seed':config['selection']['seed'],'target_ids':turn.ids}),turns[start:index],turn))
    attempts.sort(key=lambda item:item[0])
    eligible_attempt_count = len(attempts)
    if reference_rows is not None:
        attempts = freeze_target_attempts(attempts, reference_rows)
    accepted=[]; quarantined=[]; arms=[]; seen=set()
    session_day_starts = {messages[0].message_id for messages in grouped.values() if messages}
    schema=read_json(PROJECT_ROOT/'schemas/topic-reply-candidate.schema.json')
    for _,prefix,target in attempts:
        if reference_rows is None and len(accepted)>=config['selection']['max_candidates']:
            break
        try:
            complete_start = prefix[0].messages[0].message_id in session_day_starts
            candidate=build_candidate(prefix,target,tokenizer,config,prefix_complete_start=complete_start)
            jsonschema.validate(candidate,schema)
            duplicate=digest(candidate['messages'])
            if duplicate in seen:
                raise TopicContractError('duplicate_window')
            # Same targets and template, three data-construction arms.
            comparisons={arm:build_candidate(prefix,target,tokenizer,config,arm=arm,
                                             prefix_complete_start=complete_start) for arm in ('window8','causal')}
            arms.append({'target_message_ids':target.ids,'topic_candidate_sha256':candidate['candidate_sha256'],
                         'arms':{arm:{'messages':row['messages'],'candidate_sha256':row['candidate_sha256'],'tokens':row['tokens']}
                                 for arm,row in comparisons.items()}})
            seen.add(duplicate); accepted.append(candidate)
        except TopicContractError as exc:
            quarantined.append({'target_message_ids':target.ids,'reason':str(exc)})
    if not accepted:
        raise TopicContractError('no valid pilot candidates')
    selected_ids=[row['sample_id'] for row in accepted]
    identity={'version':PILOT_VERSION,'source_manifest_sha256':source_manifest['manifest_sha256'],
              'input_digests':inputs,'implementation_digests':implementation,
              'policy_sha256':digest(config),'tokenizer':tokenizer_info,'selected_days':days,
              'candidate_digests':[row['candidate_sha256'] for row in accepted]}
    if reference_pilot is not None:
        identity['reference_pilot_manifest_sha256'] = file_digest(reference_pilot / 'manifest.json')
        identity['frozen_target_population_sha256'] = digest([row['target_message_ids'] for row in reference_rows])
    pilot_id='topic-pilot_'+digest(identity)[:20]
    report={'schema_version':'daily-topic-pilot-report-v1', 'status':'structural_checks_passed',
            'counts':{**coverage,'selected_days':len(days),'eligible_target_attempts':eligible_attempt_count,
                      'candidates':len(accepted),'quarantined_attempts':len(quarantined),
                      'unprocessed_attempts':eligible_attempt_count-len(accepted)-len(quarantined)},
            'frozen_target_population_count':len(reference_rows) if reference_rows is not None else None,
            'replacement_targets_used':False if reference_rows is not None else None,
            'structural_skips':dict(universe_stats),'quarantine_reasons':dict(Counter(row['reason'] for row in quarantined)),
            'topic_counts':dict(Counter(row['topic']['topic_category'] for row in accepted)),
            'token_summary':{key:{'min':min(row['tokens'][key] for row in accepted),
                                 'max':max(row['tokens'][key] for row in accepted)}
                             for key in ('prompt_tokens','target_tokens','serialized_tokens')},
            'future_context_count':0,'cross_split_context_count':0,'target_overlap_count':0,
            'privacy_hard_leak_count':0,'canary_hit_count':0,'silent_truncation_count':0,
            'target_text_used_for_context_selection':False,'test_body_materialized':False,
            'historical_test_status':audit['historical_test_status'],
            'machine_quality_review_completed':False,'human_review_completed':False,
            'formal_training_eligible':False,'training_run':False,
            'comparison_target_count':len(arms),
            'comparison_changed_context_count':sum(row['arms']['window8']['messages'][1]!=candidate['messages'][1] for row,candidate in zip(arms,accepted)),
            'training_readiness':'blocked_pending_quality_review_validation_partition_and_versioned_driver_admission'}
    summary={'pilot_id':pilot_id,'output_dir':str(output_root/pilot_id),'status':'planned',
             'pilot_digest':digest(identity),'report':report,'source_audit':audit,
             'external_requests':0,'training_started':False}
    if not execute:
        return summary
    output=output_root/pilot_id
    if output.exists():
        manifest=read_json(output/'manifest.json')
        if manifest['identity']!=identity or any(file_digest(output/name)!=sha for name,sha in manifest['output_digests'].items()):
            raise TopicContractError('existing pilot identity/digest mismatch')
        return {**summary,'status':'already_built'}
    output_root.mkdir(parents=True,exist_ok=True,mode=0o700); output_root.chmod(0o700)
    staging=Path(tempfile.mkdtemp(prefix='.topic-pilot-',dir=output_root))
    try:
        write_json(staging/'policy.json',config)
        write_json(staging/'source-audit.json',audit)
        write_json(staging/'report.json',report)
        write_json(staging/'selection.json',{'selected_days':days,'sample_ids':selected_ids,'seed':config['selection']['seed'],
                   'target_message_ids':[row['target_message_ids'] for row in accepted],
                   'frozen_target_message_ids':([row['target_message_ids'] for row in reference_rows]
                                                if reference_rows is not None else None)})
        write_jsonl(staging/'candidates.jsonl',accepted)
        write_jsonl(staging/'quarantine.jsonl',quarantined)
        write_jsonl(staging/'comparison-arms.jsonl',arms)
        write_jsonl(staging/'daily-bundles.jsonl',[{'schema_version':'daily-topic-bundle-v1',
            'owner_scope':audit['owner_scope'],'split':'train','day':day,'session_id':sid,
            'messages':[asdict(row) for row in messages]} for (sid,day),messages in sorted(grouped.items())])
        write_review_page(staging/'review.html',accepted)
        manifest={'schema_version':'daily-topic-pilot-manifest-v1','identity':identity,'pilot_id':pilot_id,
                  'training_run':False,'formal_training_eligible':False,
                  'code_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT_ROOT,text=True).strip(),
                  'output_digests':{path.name:file_digest(path) for path in sorted(staging.iterdir())}}
        manifest['manifest_sha256']=digest(manifest)
        write_json(staging/'manifest.json',manifest)
        if any(file_digest(Path(path))!=sha for path,sha in inputs.items()):
            raise TopicContractError('source changed during pilot build')
        _publish_directory_noreplace(staging,output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {**summary,'status':'built'}
