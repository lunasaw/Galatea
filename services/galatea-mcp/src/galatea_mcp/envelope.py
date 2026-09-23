"""Transport-independent tool receipts and bounded, safe exception normalization."""
from __future__ import annotations

import json
import uuid
from .errors import DomainError

MAX_RESPONSE_BYTES = 256 * 1024
MUTATIONS = {'plan_run', 'submit_job', 'stop_job', 'cancel_campaign', 'freeze_candidate', 'verify_candidate'}


def error_envelope(request_id, category, next_action='reconcile', *, state_changed=False):
    return {'schema_version': 'galatea.tools/v1', 'request_id': request_id, 'ok': False,
            'error': {'category': category, 'retryable': False, 'state_changed': state_changed,
                      'operation_id': None, 'next_action': next_action}}


def bound_envelope(payload, maximum=MAX_RESPONSE_BYTES):
    try:
        if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()) <= maximum:
            return payload
        category, action = 'response-too-large', 'reduce-page-size-and-reconcile'
    except (ValueError, TypeError):
        category, action = 'backend-or-state-unavailable', 'reconcile'
    return error_envelope(payload['request_id'], category, action, state_changed='unknown')


def call_envelope(service, principal, name, arguments, *, request_id=None):
    payload = {'schema_version': 'galatea.tools/v1', 'request_id': request_id or 'req-' + uuid.uuid4().hex}
    try:
        payload.update(ok=True, data=service.call(principal, name, arguments))
    except DomainError as exc:
        payload.update(ok=False, error=exc.details)
    except Exception:
        return error_envelope(payload['request_id'], 'backend-or-state-unavailable', state_changed='unknown')
    return bound_envelope(payload)
