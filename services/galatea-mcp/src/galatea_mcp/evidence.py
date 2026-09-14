"""API-derived, stage-scoped evidence. Never deserialize models in this process."""
from __future__ import annotations
import json
import base64
import math
from .errors import DomainError
from .projects import relative_path
from .service import page
from .state import digest

MAX_EVIDENCE = 64 * 1024
MAX_ARTIFACT = 8 * 1024 * 1024 * 1024


class EvidenceService:
    def __init__(self, service):
        self.service, self.backend = service, service.evidence

    def owned(self, c, run_id):
        op = next((o for o in c['operations'].values() if o['run_id'] == run_id), None)
        if not op:
            raise DomainError('forbidden')
        p = self.service.registry.get(c['spec']['project_id'])
        try:
            run = self.backend.get_run(run_id)
        except Exception as exc:
            raise DomainError('evidence-unavailable', retryable=True, next_action='observe') from exc
        expected = {'run_id': run_id, 'project_id': p.project_id, 'campaign_id': c['spec']['campaign_id'],
                    'experiment_id': p.experiment_id, 'operation_id': op['operation_id'], 'role': op['role']}
        if any(run.get(k) != v for k, v in expected.items()):
            raise DomainError('forbidden')
        if op['role'] == 'evaluate' and c['stage'] not in {'final_evaluation', 'delivery'}:
            raise DomainError('forbidden')
        return p, op, run

    @staticmethod
    def metrics(run):
        prefix = ('test_',) if run['role'] == 'evaluate' else ('train_', 'val_')
        return {k: v for k, v in run.get('metrics', {}).items()
                if k.startswith(prefix) and type(v) in {int, float} and math.isfinite(v)}

    def summary(self, c, run_id):
        p, op, run = self.owned(c, run_id)
        return {key: run[key] for key in ['run_id', 'role', 'status', 'operation_id']} | {'metrics': self.metrics(run)}

    def verify_run(self, c, run_id):
        p, op, run = self.owned(c, run_id)
        if run['status'] != 'FINISHED' or op['execution'] != 'succeeded':
            raise DomainError('evidence-pending', retryable=True, next_action='observe')
        try:
            raw = self.backend.read_artifact(run_id, 'reports/evidence.json', MAX_EVIDENCE)
            report = json.loads(raw)
            if report['schema_version'] != 'galatea.evidence/v1':
                raise ValueError('schema')
            expected = {'project_id': p.project_id, 'campaign_id': c['spec']['campaign_id'],
                        'operation_id': op['operation_id'], 'submission_id': op['submission_id'],
                        'config_id': op['config_id'], 'config_digest': p.configs[op['config_id']].sha256,
                        'release_id': op['release_id'], 'release_digest': p.releases[op['release_id']].sha256,
                        'dataset_digest': p.dataset.manifest_digest, 'split_digest': p.dataset.split_digest,
                        'preprocessing': p.dataset.preprocessing, 'metric_definition': p.metric_definition,
                        'evaluation_protocol': p.evaluation_protocol, 'seed': p.configs[op['config_id']].seed,
                        'role': op['role'], 'readiness_digest': op['readiness_digest'],
                        'candidate_id': op.get('candidate_id'), 'champion_run_id': op.get('champion_run_id')}
            if any(report['lineage'].get(k) != v for k, v in expected.items()):
                raise DomainError('incompatible-evidence')
            if op['role'] != 'evaluate' and report.get('final_test_status') != 'not-run':
                raise DomainError('test-boundary-violation')
            if op['role'] == 'champion' and report['lineage'].get('clean_start') is not True:
                raise DomainError('champion-not-clean')
            if report['integrity'].get('roundtrip') is not True or report['integrity'].get('load_verified') is not True:
                raise DomainError('artifact-integrity')
            artifacts = report['artifacts']
            if not isinstance(artifacts, list) or not artifacts or len(artifacts) > 32:
                raise ValueError('artifact manifest')
            paths = set()
            sanitized_artifacts = []
            for item in artifacts:
                path = str(relative_path(item['path']))
                if path not in p.artifact_paths or path in paths or path == 'reports/evidence.json':
                    raise DomainError('artifact-integrity')
                paths.add(path)
                size, sha = item['size_bytes'], item['sha256']
                if type(size) is not int or size < 0 or size > MAX_ARTIFACT or not isinstance(sha, str) or len(sha) != 64:
                    raise ValueError('artifact metadata')
                if not self.backend.artifact_digest(run_id, path, sha, size):
                    raise DomainError('artifact-integrity')
                sanitized_artifacts.append({'path': path, 'sha256': sha, 'size_bytes': size})
            if p.model_artifact_path not in paths:
                raise DomainError('artifact-integrity')
            values = self.metrics(run)
            if any(report['metrics'].get(k) != v for k, v in values.items()):
                raise DomainError('metric-evidence-mismatch')
            if op['role'] != 'evaluate' and p.objective.metric not in values:
                raise DomainError('objective-missing')
            evidence = {'schema_version': 'galatea.verified-evidence/v1', 'run_id': run_id,
                        'lineage': expected | {'clean_start': report['lineage'].get('clean_start'),
                                              'model_sha256': report['lineage'].get('model_sha256')},
                        'metrics': values, 'artifacts': sanitized_artifacts, 'integrity': 'verified'}
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError('evidence-invalid', next_action='inspect-platform-artifacts') from exc
        evidence['evidence_digest'] = digest(evidence)
        op['integrity'] = 'verified'
        op['evidence_refs'] = [f"runs:/{run_id}/{item['path']}" for item in sanitized_artifacts]
        self.service.save(c)
        return evidence

    def call(self, name, c, args, principal_id):
        if name == 'query_runs':
            items = []
            for op in c['operations'].values():
                if op['run_id'] and (args.get('role') is None or args['role'] == op['role']):
                    items.append(self.summary(c, op['run_id']))
            return page(items, args, principal_id)
        if name == 'get_metric_history':
            _, _, run = self.owned(c, args['run_id'])
            if args['metric'] not in self.metrics(run):
                raise DomainError('forbidden')
            binding = digest({'principal': principal_id, 'scope': c['spec']['campaign_id'],
                              'run_id': args['run_id'], 'metric': args['metric']})
            token = None
            if args.get('cursor'):
                try:
                    cursor = json.loads(base64.urlsafe_b64decode(args['cursor']))
                    if cursor['binding'] != binding or not isinstance(cursor['token'], str):
                        raise ValueError('scope')
                    token = cursor['token']
                except Exception as exc:
                    raise DomainError('invalid-cursor') from exc
            result = self.backend.history_page(args['run_id'], args['metric'],
                                                limit=args.get('limit', 50), cursor=token)
            clean = [{k: value[k] for k in ['step', 'timestamp', 'value']} for value in result['items']
                     if type(value.get('value')) in {int, float} and math.isfinite(value['value'])]
            next_token = result['next_cursor']
            encoded = base64.urlsafe_b64encode(json.dumps({'binding': binding, 'token': next_token}).encode()).decode() if next_token else None
            return {'items': clean, 'next_cursor': encoded}
        if name == 'get_artifact':
            path = str(relative_path(args['artifact_ref']))
            p, _, _ = self.owned(c, args['run_id'])
            if path not in p.artifact_paths:
                raise DomainError('forbidden')
            evidence = self.verify_run(c, args['run_id'])
            if path == 'reports/evidence.json':
                return evidence
            artifact = next((a for a in evidence['artifacts'] if a['path'] == path), None)
            if not artifact:
                raise DomainError('forbidden')
            return artifact | {'artifact_ref': f"runs:/{args['run_id']}/{path}", 'verified': True,
                               'download_method': 'authorized-mlflow-artifact-api'}
        if name == 'compare_runs':
            return self.compare(c, args['run_ids'])
        if name == 'freeze_candidate':
            return self.freeze(c, args)
        if name == 'verify_candidate':
            return self.delivery(c, args['candidate_id'])
        raise DomainError('unsupported')

    def compare(self, c, run_ids):
        p = self.service.registry.get(c['spec']['project_id'])
        ranking, rejected = [], []
        for rid in run_ids:
            try:
                _, op, _ = self.owned(c, rid)
                if op['role'] not in {'baseline', 'trial'}:
                    raise DomainError('selection-role-forbidden')
                evidence = self.verify_run(c, rid)
                ranking.append({'run_id': rid, 'value': evidence['metrics'][p.objective.metric],
                                'evidence_digest': evidence['evidence_digest']})
            except DomainError as exc:
                if exc.details['category'] == 'forbidden':
                    raise
                rejected.append({'run_id': rid, 'reason': exc.details['category']})
        ranking.sort(key=lambda r: (r['value'] if p.objective.direction == 'min' else -r['value'], r['run_id']))
        return {'objective': p.objective.model_dump(), 'ranking': ranking, 'rejected': rejected,
                'claim': 'best-observed-compatible-validation-only'}

    def freeze(self, c, args):
        self.service.allowed(c)
        _, operation, _ = self.owned(c, args['run_id'])
        project = self.service.registry.get(c['spec']['project_id'])
        if project.configs[operation['config_id']].promotable is not True:
            raise DomainError('candidate-ineligible')
        result = self.compare(c, [args['run_id']])
        if not result['ranking']:
            raise DomainError('candidate-ineligible')
        observed = result['ranking'][0]
        if observed['evidence_digest'] != args['evidence_digest']:
            raise DomainError('evidence-digest-mismatch')
        if c['candidate']:
            if c['candidate']['run_id'] == args['run_id'] and c['candidate']['evidence_digest'] == args['evidence_digest']:
                return c['candidate']
            raise DomainError('candidate-frozen')
        if c['stage'] != 'search' or any(op['execution'] in {'pending','submitting','unknown','queued','running','stopping'} for op in c['operations'].values()):
            raise DomainError('stage-not-ready')
        _, op, _ = self.owned(c, args['run_id'])
        c['candidate'] = {'candidate_id': 'candidate-' + digest(observed)[:32], **observed,
                          'config_id': op['config_id'], 'release_id': op['release_id'],
                          'seed': self.service.registry.get(c['spec']['project_id']).configs[op['config_id']].seed}
        c['stage'] = 'candidate_frozen'
        self.service.save(c)
        return c['candidate']

    def delivery(self, c, candidate_id):
        candidate = c['candidate']
        if not candidate or candidate['candidate_id'] != candidate_id:
            raise DomainError('not-found')
        p = self.service.registry.get(c['spec']['project_id'])
        outcome, final, integrity, refs, issues = 'blocked', 'not-run', 'failed', [], []
        selected = None
        # A complete clean Champion is preferred; an intact selected Trial may be best-effort.
        options = [op for op in c['operations'].values() if op['role'] == 'champion' and op['execution'] == 'succeeded']
        run_ids = [op['run_id'] for op in reversed(options) if op['run_id']] + [candidate['run_id']]
        for rid in run_ids:
            try:
                selected = self.verify_run(c, rid)
                outcome, integrity = 'best-effort', 'verified'
                refs = [f"runs:/{rid}/{a['path']}" for a in selected['artifacts']]
                if selected['lineage']['role'] == 'champion':
                    c['champion_run_id'] = rid
                    c['champion_model_sha256'] = next(a['sha256'] for a in selected['artifacts'] if a['path'] == p.model_artifact_path)
                    if c['stage'] == 'champion_training':
                        c['stage'] = 'champion_ready'
                break
            except DomainError as exc:
                issues.append(exc.details['category'])
        evaluates = [op for op in c['operations'].values() if op['role'] == 'evaluate']
        if evaluates and selected and selected['lineage']['role'] == 'champion':
            op = evaluates[0]
            if op['execution'] in {'failed', 'stopped'}:
                final = 'failed'
            if op['execution'] == 'succeeded' and op['run_id']:
                try:
                    test = self.verify_run(c, op['run_id'])
                    model_sha = next(a['sha256'] for a in selected['artifacts'] if a['path'] == p.model_artifact_path)
                    if test['lineage']['champion_run_id'] != selected['run_id'] or test['lineage']['model_sha256'] != model_sha:
                        raise DomainError('evaluation-model-mismatch')
                    passed = bool(p.quality_gates) and all(
                        gate.metric in test['metrics'] and
                        (test['metrics'][gate.metric] <= gate.threshold if gate.direction == 'min' else test['metrics'][gate.metric] >= gate.threshold)
                        for gate in p.quality_gates)
                    final = 'passed' if passed else 'failed'
                    outcome = 'accepted' if passed else 'best-effort'
                    refs.append(f"runs:/{op['run_id']}/reports/evidence.json")
                    c['stage'] = 'delivery'
                except DomainError as exc:
                    issues.append(exc.details['category'])
                    final = 'failed'
        report = {'candidate_id': candidate_id, 'outcome': outcome, 'integrity': integrity,
                  'final_test_status': final, 'evidence_refs': refs, 'issues': sorted(set(issues)),
                  'schema_version': 'galatea.delivery/v1', 'campaign_id': c['spec']['campaign_id']}
        report['report_ref'] = 'galatea-report:' + digest(report)
        c['report'] = report
        self.service.save(c)
        return report
