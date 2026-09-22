"""Offline accounting of self-reported scores; never fit or relabel evidence."""
from __future__ import annotations

from pathlib import Path
import tempfile

from ._common import digest, file_digest
from . import topic_axes_review as v2
from . import topic_axes_review_v3 as v3
from .datasets import _publish_directory_noreplace
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json, write_json
from .topic_context import TopicContractError
from .topic_review_protocol import LINKED


def confidence_only(row: dict) -> bool:
    axes = row['axes']
    return (axes['confidence'] < .9 and axes['relation'] in LINKED and axes['context_status'] == 'sufficient'
            and axes['communicative_value'] == 'useful' and axes['risk_status'] == 'clear')


def summarize(decisions: list[dict], cases: list[dict]) -> dict:
    references = {(r['judge'], r['fixture_id']): r for r in cases}
    seen = set()
    result = {}
    for row in decisions:
        key = (row['judge'], row['sample_id'])
        if key in seen or key not in references:
            raise TopicContractError('confidence audit duplicate or unknown judgment')
        seen.add(key)
    for name in ('gpt', 'claude'):
        group = [r for r in decisions if r['judge'] == name]
        solely = [r for r in group if confidence_only(r)]
        bins = {}
        for label, lower in (('below_0_9', True), ('at_least_0_9', False)):
            selected = [r for r in group if (r['axes']['confidence'] < .9) == lower]
            bins[label] = {'reviewed': len(selected),
                           'frozen_core_matches': sum(references[(name, r['sample_id'])]['core_agrees'] for r in selected),
                           'false_keeps': sum(references[(name, r['sample_id'])]['false_keep'] for r in selected)}
        result[name] = {'population': sum(r['judge'] == name for r in cases), 'valid': len(group), 'score_bins': bins,
                        'confidence_only_deferred': len(solely),
                        'confidence_only_reference_keep': sum(references[(name, r['sample_id'])]['expected_status'] == 'keep' for r in solely),
                        'confidence_only_cases': [{'fixture_id': r['sample_id'], 'confidence': r['axes']['confidence'],
                                                   'original_status': r['status']} for r in solely],
                        'false_keep_cases': [{'fixture_id': r['sample_id'], 'confidence': r['axes']['confidence']}
                                            for r in group if references[(name, r['sample_id'])]['false_keep']]}
    return result


def run_audit(*, sources: dict, output_root: Path, controlled_root: Path, execute: bool = False) -> dict:
    private_path(output_root, controlled_root)
    results, inputs = {}, {}
    if set(sources) != {'v2', 'v3'}:
        raise TopicContractError('confidence audit requires distinct v2/v3 sources')
    for version, (directory, config_path) in sources.items():
        private_path(directory, controlled_root)
        if directory.resolve() == output_root.resolve() or directory.resolve() in output_root.resolve().parents:
            raise TopicContractError('confidence audit output overlaps source')
        policy = v2.load_config(config_path) if version == 'v2' else v3.load_config(config_path)
        fixture_path, fixtures = v2.load_fixtures(config_path, policy)
        manifest = verify_manifest(directory, {'identity.json', 'report.json', 'decisions.json', 'cases.json', 'budget.json'})
        identity = read_json(directory / 'identity.json')
        expected = v2.protocol_binding(policy, fixture_path, '') if version == 'v2' else v3.binding_for(policy, fixture_path, '')
        expected['endpoint_sha256'] = identity['protocol']['endpoint_sha256']
        record = read_json(directory / 'decisions.json')
        report = read_json(directory / 'report.json')
        if (identity != manifest['identity'] or identity['scope'] != 'calibration' or identity['protocol'] != expected
                or record['identity'] != identity or report['decisions_sha256'] != digest(record['decisions'])):
            raise TopicContractError('confidence source binding mismatch')
        candidates = [v2.fixture_candidate(f) for f in fixtures]
        if identity['population_sha256'] != digest([r['candidate_sha256'] for r in candidates]):
            raise TopicContractError('confidence source population mismatch')
        if version == 'v3':
            v3.validate_v3_decisions(candidates, record['decisions'], policy, identity['route']['bound_returned_models'])
        summary = v2.calibration_summary(fixtures, record['decisions'], policy)
        cases = summary.pop('cases')
        if (any(report.get(k) != v for k, v in summary.items())
                or read_json(directory / 'cases.json') != {'cases': cases}):
            raise TopicContractError('confidence source statistics changed')
        results[version] = summarize(record['decisions'], cases)
        inputs[str(directory / 'manifest.json')] = file_digest(directory / 'manifest.json')
        inputs[str(config_path.resolve())] = file_digest(config_path)
    identity = {'method': 'topic-confidence-accounting-v1', 'inputs': inputs,
                'implementation_sha256': file_digest(Path(__file__))}
    directory = output_root / ('topic-confidence-audit_' + digest(identity)[:20])
    report = {'status': 'complete' if execute else 'planned', 'workspace': str(directory), 'cohorts': results,
              'cohorts_pooled': False, 'reference_origin': 'developer_authored_synthetic_not_human_truth',
              'probability_calibration_established': False, 'replacement_rule_validated': False,
              'calibration_fit_performed': False, 'old_gates_changed': False, 'existing_labels_changed': False,
              'external_requests': 0, **v2.GOVERNANCE}
    if not execute:
        return report
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.exists():
        manifest = verify_manifest(directory, {'identity.json', 'report.json'})
        if manifest['identity'] != identity or read_json(directory / 'report.json') != report:
            raise TopicContractError('confidence audit replay mismatch')
        return report
    staging = Path(tempfile.mkdtemp(prefix='.confidence-', dir=output_root))
    write_json(staging / 'identity.json', identity)
    write_json(staging / 'report.json', report)
    manifest = {'schema_version': 'topic-confidence-audit-manifest-v1', 'identity': identity, **v2.GOVERNANCE,
                'output_digests': {name: file_digest(staging / name) for name in ('identity.json', 'report.json')}}
    manifest['manifest_sha256'] = digest(manifest)
    write_json(staging / 'manifest.json', manifest)
    _publish_directory_noreplace(staging, directory)
    return report
