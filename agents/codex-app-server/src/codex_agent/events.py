"""Console observation entry point; all event history lives in process memory."""
from .console.service import ConsoleObservationService

EventLog = ConsoleObservationService

# Existing Registry error-detail boundary; independent of Console projection.
SENSITIVE_KEYS = {'token', 'secret', 'password', 'api_key', 'authorization', 'prompt',
                  'reasoning', 'test_sample', 'test_label', 'raw_log', 'signed_url'}


def _sensitive(key):
    normalized = key.lower()
    return normalized in SENSITIVE_KEYS or any(fragment in normalized for fragment in
                                               ('_token', '_secret', 'credential', 'private_key'))
