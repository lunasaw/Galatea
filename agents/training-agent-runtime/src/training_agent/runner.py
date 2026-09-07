"""Bounded serial decisions; external work always reconciled before inference."""
import hashlib
import json
from .runtime.types import TurnRequest, TurnOutcome
from .runtime.output import ACTIVE, SCHEMA, validate_proposal
from .runtime.usage import usage_delta

def initial_state(project_id, campaign_id, revision, workspace, skill_path, runtime_digest, prompt):
    if type(revision) is not int or revision < 1: raise ValueError('Positive revision required')
    return dict(schema_version=1,project_id=project_id,campaign_id=campaign_id,request_revision=revision,
                decision_seq=0,runner_state='ready',thread_id=None,turn_id=None,workspace=workspace,
                skill_path=skill_path,runtime_digest=runtime_digest,prompt=prompt,operation_ids=[],
                last_observation_digest=None,next_check_at=0,retry_count=0,usage_snapshot=None,
                usage_unknown=False,observed_tokens=0,cancel_requested=False,last_error=None,
                max_turns=20,token_threshold=100000,continue_count=0,repair_count=0,user_response=None)

def observation_digest(campaign, operations):
    facts = {'revision':campaign['request_revision'],'report':campaign.get('report'),
             'candidate':campaign.get('candidate'), 'budget':campaign.get('budget'),
             'operations':[{k:op.get(k) for k in ('operation_id','execution','quality','integrity')} for op in operations]}
    return hashlib.sha256(json.dumps(facts,sort_keys=True).encode()).hexdigest()

def accepted_report(report):
    return bool(report and report.get('report_ref') and report.get('integrity')=='verified' and (
        report.get('outcome')=='best-effort' or
        (report.get('outcome')=='accepted' and report.get('final_test_status')=='passed')))

def candidate_id(candidate):
    if isinstance(candidate,str): return candidate
    if isinstance(candidate,dict): return candidate.get('candidate_id')
    return None

class Runner:
    def __init__(self, store, campaign_id, platform, runtime):
        self.store, self.campaign_id, self.platform, self.runtime = store,campaign_id,platform,runtime

    def tick(self, now):
        state = self.store.load(self.campaign_id)
        def save(): self.store.save(self.campaign_id,state)
        def stop(reason):
            state.update(runner_state='blocked',last_error=reason); save()
        if hasattr(self.runtime,'ensure_clean'):
            try: self.runtime.ensure_clean()
            except Exception: stop('Unreconciled worker process group'); return
        if state['runner_state']=='agent_running':
            state['usage_unknown']=True
            if hasattr(self.runtime,'recover_identity'):
                try:
                    recovered=self.runtime.recover_identity(state)
                    if recovered: state.update(thread_id=recovered[0],turn_id=recovered[1])
                except ValueError: stop('Worker journal mismatch'); return
            save()
        try:
            campaign, operations = self.platform.snapshot(state)
        except Exception:
            state.update(runner_state='retry_wait',retry_count=state['retry_count']+1,
                         next_check_at=now+min(300,60*2**min(state['retry_count'],3)),last_error='Platform unavailable')
            save(); return
        state['operation_ids'] = [op['operation_id'] for op in operations]
        active = [op for op in operations if op['execution'] in ACTIVE]
        if state['cancel_requested'] or campaign.get('cancelled'):
            try:
                self.platform.cancel(state)
                if hasattr(self.runtime,'interrupt'): self.runtime.interrupt()
                for op in active: self.platform.stop(state,op['operation_id'])
                state.update(runner_state='cancelling' if active else 'cancelled',next_check_at=now+60)
            except Exception: state.update(runner_state='cancelling',last_error='Cancellation reconciliation unavailable')
            save(); return
        if active:
            state.update(runner_state='waiting_external',next_check_at=now+60); save(); return
        report=campaign.get('report') or {}
        exhausted=state['usage_unknown'] or state['decision_seq'] >= state['max_turns'] or state['observed_tokens'] >= state['token_threshold']
        if exhausted and not report:
            frozen_candidate=candidate_id(campaign.get('candidate'))
            if frozen_candidate and hasattr(self.platform,'verify_candidate'):
                try: report=self.platform.verify_candidate(state,frozen_candidate)
                except Exception:
                    state.update(runner_state='waiting_evidence',next_check_at=now+60,
                                 last_error='Candidate verification unavailable'); save(); return
        if exhausted and accepted_report(report):
            state.update(runner_state='completed',report_ref=report['report_ref'],outcome=report['outcome']); save(); return
        if state['runner_state'] in ('completed','cancelled','blocked'): save(); return
        evidence_pending=any(op.get('execution')=='succeeded' and op.get('integrity','pending')=='pending'
                             for op in operations) and not report
        if evidence_pending:
            state.update(runner_state='waiting_evidence',next_check_at=now+60,
                         last_observation_digest=observation_digest(campaign,operations),
                         last_error='Terminal operation evidence is not ready')
            save(); return
        if state['usage_unknown']: stop('Unknown model usage; explicit reconciliation required'); return
        if campaign['request_revision'] != state['request_revision']:
            revision_advanced=(type(campaign['request_revision']) is int and
                               campaign['request_revision'] > state['request_revision'])
            approval_resume=(state['runner_state']=='awaiting_approval' and state.get('user_response'))
            if revision_advanced and approval_resume:
                state['request_revision']=campaign['request_revision']
                state['last_observation_digest']=None
                save()
            else:
                stop('Campaign revision changed without a matching approval response'); return
        digest = observation_digest(campaign,operations)
        response = state.get('user_response')
        if state['runner_state'] in ('awaiting_input','awaiting_approval'):
            if not response: return
            if state['runner_state']=='awaiting_approval' and digest == state['last_observation_digest']: return
        elif digest == state['last_observation_digest'] and state['runner_state'] != 'ready': return
        if now < state['next_check_at']: return
        if state['decision_seq'] >= state['max_turns'] or state['observed_tokens'] >= state['token_threshold']:
            stop('Decision budget exhausted'); return
        state.update(runner_state='agent_running',decision_seq=state['decision_seq']+1,last_observation_digest=digest)
        save()
        def identity(thread,turn):
            state.update(thread_id=thread,turn_id=turn); save()
        request = TurnRequest(state['campaign_id'],state['request_revision'],state['decision_seq'],
                              state['workspace'],state['thread_id'],state['skill_path'],
                              json.dumps({'request':state['prompt'],'response':response,'campaign':campaign,
                                          'operations':operations,'decision_seq':state['decision_seq']}),SCHEMA)
        try: outcome = self.runtime.run_turn(request,identity)
        except Exception: outcome = TurnOutcome(state['thread_id'],state['turn_id'],'failed',error='Worker failed')
        delta = usage_delta(state['usage_snapshot'],outcome.usage_snapshot)
        if delta is None: state['usage_unknown'] = True
        else:
            state['observed_tokens'] += delta
            state['usage_snapshot'] = outcome.usage_snapshot
        state['user_response'] = None
        save()
        try: campaign,operations = self.platform.snapshot(state)
        except Exception:
            state.update(runner_state='retry_wait',last_error='Post-turn reconciliation unavailable',next_check_at=now+60); save(); return
        state['operation_ids'] = [op['operation_id'] for op in operations]
        if any(op['execution'] in ACTIVE for op in operations):
            state.update(runner_state='waiting_external',next_check_at=now+60); save(); return
        if state['usage_unknown']: stop('Unknown model usage'); return
        try:
            if outcome.status != 'completed': raise ValueError('Runtime failed')
            proposal = validate_proposal(outcome.final_json,state,campaign,operations)
        except ValueError:
            state['repair_count'] += 1
            state.update(runner_state='ready' if state['repair_count'] <= 1 else 'blocked',
                         last_error='Invalid proposal after platform reconciliation',next_check_at=now+60)
            save(); return
        action = proposal['action']
        state['last_observation_digest'] = observation_digest(campaign,operations)
        state['last_proposal'] = proposal
        state['last_error'] = None
        if action == 'complete': state['runner_state']='completed'
        elif action in ('request_input','request_approval'):
            state['runner_state']='awaiting_input' if action=='request_input' else 'awaiting_approval'
        elif action == 'blocked': state['runner_state']='blocked'
        else:
            state['continue_count'] += 1
            state['runner_state']='ready' if state['continue_count'] <= 2 else 'blocked'
        state['next_check_at']=now+60
        save()
