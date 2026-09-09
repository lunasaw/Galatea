"""Serial orchestration. All durable admission precedes external side effects."""
from __future__ import annotations
import base64
import copy
import json
import time
from pathlib import Path
from typing import Callable
import jsonschema
from pydantic import ValidationError
from .contracts import CampaignSpec, TOOLS
from .errors import DomainError
from .projects import trusted_file
from .state import SCHEMA, canonical, digest

ACTIVE = {'pending', 'submitting', 'unknown', 'queued', 'running', 'stopping'}
TERMINAL = {'succeeded', 'failed', 'stopped'}


def page(items, args, binding):
    limit = args.get('limit', 50)
    cursor = args.get('cursor')
    offset = 0
    expected = digest({'schema_version': 'galatea.cursor/v1', 'binding': binding,
                       'filter': {k: v for k, v in args.items() if k not in {'cursor', 'limit'}}})
    if cursor:
        try:
            data = json.loads(base64.urlsafe_b64decode(cursor))
            offset = data['offset']
            if data['binding'] != expected or type(offset) is not int or offset < 0:
                raise ValueError()
        except Exception as exc:
            raise DomainError('invalid-cursor') from exc
    end = offset + limit
    next_cursor = base64.urlsafe_b64encode(canonical({'offset': end, 'binding': expected})).decode() if end < len(items) else None
    return {'items': items[offset:end], 'next_cursor': next_cursor}


class Service:
    def __init__(self, store, registry, ray, evidence, *, clock: Callable = time.time):
        self.store, self.registry, self.ray, self.evidence, self.clock = store, registry, ray, evidence, clock

    def register(self, raw):
        """Trusted offline admin operation, never an MCP tool."""
        with self.store.mutex:
            try:
                spec = CampaignSpec.model_validate(raw)
            except ValidationError as exc:
                raise DomainError('invalid-campaign') from exc
            p = self.registry.get(spec.project_id)
            for slot in spec.slots:
                if not slot.config_ids or not slot.release_ids:
                    raise DomainError('invalid-campaign')
                if not set(slot.config_ids) <= set(p.configs) or not set(slot.release_ids) <= set(p.releases):
                    raise DomainError('unapproved-input')
            normalized = spec.model_dump()
            try:
                existing = self.store.read('campaigns', spec.campaign_id)
            except DomainError as exc:
                if exc.details['category'] != 'not-found':
                    raise
            else:
                if existing['spec'] == normalized:
                    return
                raise DomainError('campaign-exists', next_action='use-reviewed-revision')
            self.store.save('campaigns', spec.campaign_id, {
                'schema_version': SCHEMA, 'spec': normalized, 'stage': 'baseline', 'cancelled': False,
                'plans': {}, 'operations': {}, 'candidate': None, 'report': None,
                'reserved': {'cpu_seconds': 0, 'gpu_seconds': 0}})

    def amend(self, campaign_id, changes):
        """Trusted offline amendment. No model tool can write authorization."""
        with self.store.mutex:
            c = self.store.read('campaigns', campaign_id)
            if set(changes) != {'request_revision', 'expires_at', 'approved_by', 'budget'}:
                raise DomainError('invalid-amendment')
            try:
                revised = CampaignSpec.model_validate(c['spec'] | changes).model_dump()
            except ValidationError as exc:
                raise DomainError('invalid-amendment') from exc
            if revised['request_revision'] <= c['spec']['request_revision']:
                raise DomainError('revision-not-increased')
            if c['cancelled']:
                raise DomainError('cancelled')
            if any(op['execution'] in ACTIVE for op in c['operations'].values()):
                raise DomainError('active-operation')
            if any(revised['budget'][key] < c['spec']['budget'][key] for key in revised['budget']):
                raise DomainError('budget-cannot-decrease')
            if len(c.setdefault('amendments', [])) >= 32:
                raise DomainError('amendment-limit')
            c['amendments'].append({'prior_spec_digest': digest(c['spec']), 'changes': changes})
            c['spec'] = revised
            self.save(c)

    def call(self, principal, tool, args):
        if tool not in TOOLS:
            raise DomainError('unsupported')
        try:
            jsonschema.Draft202012Validator(TOOLS[tool]).validate(args)
            canonical(args)
        except (jsonschema.ValidationError, ValueError, TypeError) as exc:
            raise DomainError('invalid-input') from exc
        principal.check(tool, args.get('project_id'), args.get('campaign_id'))
        with self.store.mutex:
            name = tool.removeprefix('galatea_')
            if name == 'get_capabilities':
                return {'protocol_version': 'galatea.tools/v1', 'tools': list(TOOLS),
                        'configuration_mode': 'prebuilt', 'budget_mode': 'conservative-ceiling',
                        'max_active_jobs': 1, 'max_page_size': 100, 'log_bytes': 0,
                        'raw_logs': False, 'pause_resume': False, 'promotion': False,
                        'uploads': False, 'dynamic_builds': False}
            if name == 'list_projects':
                rows = [self.registry.inspect(p) for p in sorted(principal.project_ids) if p in self.registry.projects]
                return page(rows, args, principal.principal_id)
            if name == 'inspect_project':
                return self.registry.inspect(args['project_id'])
            campaign = self.store.read('campaigns', args['campaign_id'])
            if campaign['spec']['project_id'] != args['project_id']:
                raise DomainError('forbidden')
            if name == 'get_campaign':
                return self.summary(campaign)
            if name == 'list_operations':
                return page([self.public_op(op) for op in campaign['operations'].values()], args, principal.principal_id)
            if name in {'get_operation', 'observe_job', 'stop_job'}:
                op = self.operation(campaign, args['operation_id'])
                if name == 'observe_job':
                    self.observe(campaign, op)
                elif name == 'stop_job':
                    self.stop(campaign, op)
                return self.public_op(op)
            if name == 'plan_run':
                return self.plan(campaign, args)
            if name == 'submit_job':
                return self.submit(campaign, args)
            if name == 'cancel_campaign':
                campaign['cancelled'] = True
                campaign['cancel_reason'] = args['reason']
                self.save(campaign)  # A still-running Codex turn can no longer admit compute.
                for op in campaign['operations'].values():
                    if op['execution'] in ACTIVE:
                        self.stop(campaign, op)
                return self.summary(campaign)
            from .evidence import EvidenceService
            return EvidenceService(self).call(name, campaign, args, principal.principal_id)

    def save(self, campaign):
        self.store.save('campaigns', campaign['spec']['campaign_id'], campaign)

    def summary(self, c):
        return {'campaign_id': c['spec']['campaign_id'], 'project_id': c['spec']['project_id'],
                'request_revision': c['spec']['request_revision'], 'stage': c['stage'],
                'expires_at': c['spec']['expires_at'], 'cancelled': c['cancelled'],
                'budget': {**c['spec']['budget'], **{'reserved_' + k: v for k, v in c['reserved'].items()},
                           **{'remaining_' + k: c['spec']['budget'][k] - v for k, v in c['reserved'].items()}},
                'slots': c['spec']['slots'], 'candidate': c['candidate'], 'report': c['report']}

    @staticmethod
    def operation(c, operation_id):
        try:
            return c['operations'][operation_id]
        except KeyError as exc:
            raise DomainError('not-found') from exc

    @staticmethod
    def public_op(op):
        keys = ['operation_id', 'submission_id', 'step_id', 'attempt', 'role', 'config_id', 'release_id',
                'run_id', 'execution', 'quality', 'integrity', 'created_at', 'deadline_at', 'evidence_refs']
        return {key: op[key] for key in keys if key in op}

    def allowed(self, c):
        if c['cancelled']:
            raise DomainError('cancelled')
        if self.clock() >= c['spec']['expires_at']:
            raise DomainError('authorization-expired', next_action='request-approval')

    def slot(self, c, args):
        slot = next((s for s in c['spec']['slots'] if s['step_id'] == args['step_id']), None)
        if not slot or args['role'] != slot['role'] or args['config_id'] not in slot['config_ids'] or args['release_id'] not in slot['release_ids']:
            raise DomainError('unapproved-slot')
        if not 1 <= args['attempt'] <= slot['max_attempts']:
            raise DomainError('retry-limit')
        if args['attempt'] > 1:
            prior = [op for op in c['operations'].values() if op['step_id'] == args['step_id'] and op['attempt'] == args['attempt'] - 1]
            if len(prior) != 1 or prior[0]['execution'] != 'failed':
                raise DomainError('retry-not-confirmed')
            if any(prior[0][key] != args[key] for key in ['config_id', 'release_id', 'role']):
                raise DomainError('retry-input-conflict')
        return slot

    def stage_allowed(self, c, args):
        role = args['role']
        expected = {'baseline': {'baseline'}, 'trial': {'search'},
                    'champion': {'candidate_frozen', 'champion_training'},
                    'evaluate': {'champion_ready', 'final_evaluation'}}[role]
        if c['stage'] not in expected:
            raise DomainError('stage-not-ready')
        if role == 'champion':
            candidate = c['candidate']
            if not candidate or args['release_id'] != candidate['release_id']:
                raise DomainError('candidate-mismatch')
            project = self.registry.get(c['spec']['project_id'])
            try:
                selected = project.configs[candidate['config_id']]
                champion = project.configs[args['config_id']]
                selected_path = trusted_file(Path(project.root), selected.path)
                champion_path = trusted_file(Path(project.root), champion.path)
                selected_config = json.loads(selected_path.read_text())
                champion_config = json.loads(champion_path.read_text())
                for value in (selected_config, champion_config):
                    value.pop('run', None)
                    value.pop('evaluation', None)
                    value.get('execution', {}).get('resources', {}).pop('placement', None)
                if (selected.seed != champion.seed or selected_config != champion_config):
                    raise ValueError('configuration differs')
            except Exception as exc:
                raise DomainError('candidate-mismatch') from exc
        if role == 'evaluate' and not c.get('champion_run_id'):
            raise DomainError('champion-not-ready')

    def plan(self, c, args):
        self.allowed(c)
        self.slot(c, args)
        p, config, release, input_digest = self.registry.verify(c['spec']['project_id'], args['config_id'], args['release_id'], args['role'])
        identity = {k: args[k] for k in ['step_id', 'attempt', 'release_id', 'config_id', 'role']}
        binding = {'schema_version': 'galatea.plan/v1', **identity,
                   'project_id': p.project_id, 'campaign_id': c['spec']['campaign_id'],
                   'request_revision': c['spec']['request_revision'], 'spec_digest': digest(c['spec']),
                   'input_digest': input_digest, 'resources': config.resources.model_dump(),
                   'candidate_id': c['candidate']['candidate_id'] if c['candidate'] else None,
                   'champion_run_id': c.get('champion_run_id') if args['role'] == 'evaluate' else None,
                   'champion_model_sha256': c.get('champion_model_sha256') if args['role'] == 'evaluate' else None}
        logical = self.logical_id(c, args)
        if logical not in c['operations']:
            self.stage_allowed(c, args)
        plan_id = 'plan-' + digest(binding)[:32]
        old = c['plans'].get(plan_id)
        if len(c['plans']) >= 512 and old is None:
            raise DomainError('plan-limit')
        plan = {**binding, 'plan_id': plan_id, 'readiness_digest': digest(binding),
                'expires_at': min(int(self.clock()) + 900, c['spec']['expires_at'])}
        c['plans'][plan_id] = plan
        self.save(c)
        return copy.deepcopy(plan)

    @staticmethod
    def logical_id(c, args):
        return 'op-' + digest({'schema_version': 'galatea.operation/v1', 'campaign_id': c['spec']['campaign_id'],
                               'step_id': args['step_id'], 'attempt': args['attempt']})[:32]

    def reserve(self, c, plan, config):
        cost = config.resources.cost()
        p = self.registry.get(c['spec']['project_id'])
        reserve = {'cpu_seconds': 0, 'gpu_seconds': 0}
        # Protect maximum allowed final configuration cost, even before a candidate is selected.
        for slot in c['spec']['slots']:
            if slot['role'] not in {'champion', 'evaluate'} or slot['role'] == plan['role']:
                continue
            if any(op['role'] == slot['role'] for op in c['operations'].values()):
                continue
            for k in reserve:
                reserve[k] += max(p.configs[cid].resources.cost()[k] for cid in slot['config_ids'])
        if any(c['reserved'][k] + cost[k] + reserve[k] > c['spec']['budget'][k] for k in cost):
            raise DomainError('budget-exhausted', next_action='request-approval-or-deliver')
        for other_id in self.store.ids():
            other = self.store.read('campaigns', other_id)
            if any(op['execution'] in ACTIVE for op in other['operations'].values()):
                raise DomainError('active-operation', next_action='observe-existing')
        for k in cost:
            c['reserved'][k] += cost[k]

    def submit(self, c, args):
        self.allowed(c)
        plan = c['plans'].get(args['plan_id'])
        if not plan:
            raise DomainError('not-found')
        p, config, release, current = self.registry.verify(c['spec']['project_id'], plan['config_id'], plan['release_id'], plan['role'])
        if current != plan['input_digest'] or digest(c['spec']) != plan['spec_digest']:
            raise DomainError('stale-plan')
        operation_id = self.logical_id(c, plan)
        if operation_id in c['operations']:
            old = c['operations'][operation_id]
            if any(old[k] != plan[k] for k in ['input_digest', 'config_id', 'release_id', 'role']):
                raise DomainError('operation-conflict')
            return self.public_op(old)
        if self.clock() >= plan['expires_at']:
            raise DomainError('expired-plan')
        self.slot(c, plan)
        self.stage_allowed(c, plan)
        self.reserve(c, plan, config)
        submission_id = 'galatea-py-' + operation_id[3:]
        op = {**plan, 'operation_id': operation_id, 'submission_id': submission_id,
              'cluster_id': self.ray.cluster_id, 'run_id': None, 'execution': 'pending',
              'quality': 'not-evaluated', 'integrity': 'pending', 'evidence_refs': [],
              'created_at': int(self.clock()),
              'deadline_at': int(self.clock()) + config.resources.seconds + config.resources.cleanup_seconds,
              'metadata': {'galatea.source': 'python-mcp-v1', 'galatea.project': p.project_id,
                           'galatea.campaign': c['spec']['campaign_id'], 'galatea.operation': operation_id,
                           'galatea.submission': submission_id, 'galatea.release': plan['release_id'],
                           'galatea.readiness': plan['readiness_digest'], 'galatea.step': plan['step_id'],
                           'galatea.attempt': str(plan['attempt']), 'galatea.role': plan['role']}}
        if plan['role'] == 'evaluate':
            marker = {'schema_version': SCHEMA, 'campaign_id': c['spec']['campaign_id'],
                      'candidate_id': c['candidate']['candidate_id'], 'operation_id': operation_id,
                      'submission_id': submission_id, 'protocol_digest': digest(p.evaluation_protocol)}
            self.store.claim(p.dataset.holdout_identity, marker)
            c['stage'] = 'final_evaluation'
        elif plan['role'] == 'champion':
            c['stage'] = 'champion_training'
        c['operations'][operation_id] = op
        self.save(c)
        self.dispatch(c, op, p, config, release)
        return self.public_op(op)

    def dispatch(self, c, op, p, config, release):
        op['execution'] = 'submitting'
        self.save(c)
        try:
            self.ray.submit(copy.deepcopy(op), p, config, release)
            op['execution'] = 'queued'
        except Exception:
            op['execution'] = 'unknown'
        self.save(c)

    def observe(self, c, op):
        if op['execution'] == 'pending':
            if c['cancelled'] or self.clock() >= op['deadline_at']:
                op['execution'] = 'stopped'
                self.save(c)
                return
            p, config, release, current = self.registry.verify(c['spec']['project_id'], op['config_id'], op['release_id'], op['role'])
            if current != op['input_digest'] or digest(c['spec']) != op['spec_digest']:
                raise DomainError('stale-plan')
            self.allowed(c)
            self.dispatch(c, op, p, config, release)
        try:
            observed = self.ray.observe(op)
            if (observed is None or observed.get('cluster_id') != op['cluster_id']
                    or any(observed.get('metadata', {}).get(k) != v for k, v in op['metadata'].items())
                    or observed.get('execution') not in ACTIVE | TERMINAL):
                raise ValueError('unknown identity')
            op['execution'] = observed['execution']
            if op['execution'] in ACTIVE and (c['cancelled'] or op.get('stop_requested') or self.clock() >= op['deadline_at']):
                self.stop(c, op)
        except Exception:
            # A previously verified terminal fact remains true even after Ray history expires.
            if op['execution'] not in TERMINAL:
                op['execution'] = 'unknown'
        try:
            runs = self.evidence.for_operation(self.registry.get(c['spec']['project_id']), c, op)
            if len(runs) == 1:
                op['run_id'] = runs[0]['run_id']
        except Exception:
            pass  # Ray status and evidence readiness are separate dimensions.
        if op['execution'] == 'succeeded' and op.get('run_id'):
            from .evidence import EvidenceService
            try:
                verified = EvidenceService(self).verify_run(c, op['run_id'])
                if op['role'] == 'baseline' and c['stage'] == 'baseline':
                    c['stage'] = 'search'
                if op['role'] == 'champion' and c['stage'] == 'champion_training':
                    c['champion_run_id'] = op['run_id']
                    c['champion_model_sha256'] = next(a['sha256'] for a in verified['artifacts']
                                                       if a['path'] == self.registry.get(c['spec']['project_id']).model_artifact_path)
                    c['stage'] = 'champion_ready'
            except DomainError as exc:
                op['integrity'] = 'pending' if self.clock() < op['deadline_at'] + 300 else 'failed'
                op['evidence_error'] = exc.details['category']
        self.save(c)

    def stop(self, c, op):
        if op['execution'] in TERMINAL:
            return
        if op['execution'] == 'pending':
            op['execution'] = 'stopped'
            self.save(c)
            return
        op['stop_requested'] = True
        self.save(c)
        try:
            # Never stop an identically named operation on a different cluster.
            current = self.ray.observe(op)
            if not current or current.get('cluster_id') != op['cluster_id'] or any(current.get('metadata', {}).get(k) != v for k, v in op['metadata'].items()):
                raise ValueError('identity mismatch')
            self.ray.stop(op)
            op['execution'] = 'stopping'
        except Exception:
            op['execution'] = 'unknown'
        self.save(c)

    def reconcile_all(self):
        with self.store.mutex:
            for cid in self.store.ids():
                c = self.store.read('campaigns', cid)
                for op in c['operations'].values():
                    if op['execution'] in ACTIVE or (op['execution'] == 'succeeded' and op['integrity'] == 'pending'):
                        self.observe(c, op)
