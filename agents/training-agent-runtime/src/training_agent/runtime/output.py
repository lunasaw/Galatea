import json
from pathlib import Path
from jsonschema import Draft202012Validator

SCHEMA = json.loads(Path(__file__).with_name('agent-turn.schema.json').read_text())
ACTIVE = {'pending','submitting','unknown','queued','running','stopping'}

def validate_proposal(value, state, campaign, operations):
    errors = list(Draft202012Validator(SCHEMA).iter_errors(value))
    if errors: raise ValueError('Invalid proposal schema')
    for key in ('campaign_id','request_revision','decision_seq'):
        if value[key] != state[key]: raise ValueError('Proposal identity mismatch')
    if value['request_revision'] < 1 or value['decision_seq'] < 1:
        raise ValueError('Invalid revision')
    known = {op['operation_id'] for op in operations}
    if not set(value['operation_ids']) <= known: raise ValueError('Foreign operation')
    report = campaign.get('report') or {}
    if not set(value['evidence_refs']) <= set(report.get('evidence_refs', [])):
        raise ValueError('Unverified evidence reference')
    action = value['action']
    if action in ('request_input','request_approval'):
        if not value['user_input_request'] or not value['user_input_request'].strip():
            raise ValueError('Missing input request')
    elif value['user_input_request'] is not None: raise ValueError('Unexpected input request')
    if action == 'complete':
        if any(op['execution'] in ACTIVE for op in operations): raise ValueError('Work still active')
        if (not value['report_ref'] or value['report_ref'] != report.get('report_ref')
                or report.get('integrity') != 'verified' or value['proposed_outcome'] != report.get('outcome')
                or value['proposed_outcome'] not in ('accepted','best-effort')):
            raise ValueError('Missing verified platform report')
        if value['proposed_outcome'] == 'accepted' and report.get('final_test_status') != 'passed':
            raise ValueError('Accepted requires final test')
    elif value['report_ref'] is not None or value['proposed_outcome'] not in (None,'blocked'):
        raise ValueError('Incompatible action fields')
    if action == 'wait_external' and not value['operation_ids']:
        raise ValueError('Missing operation references')
    return value
