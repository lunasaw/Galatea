"""Model-specific retained reservations and persistent usage-envelope circuit.

Provider usage is observable only after a response. The envelope is conservative
and empirically checked, not a provider-attested upper bound on billing.
"""
from __future__ import annotations

from pathlib import Path

from . import topic_validation_review as previous
from ._common import digest
from .fact_review_server import _atomic_json
from .topic_candidates import read_json
from .topic_context import TopicContractError
from .topic_review_budget import ReviewBudget


def input_reservation(estimate: int, judge: str, guard: dict) -> int:
    if type(estimate) is not int or estimate <= 0 or judge not in guard['multipliers']:
        raise TopicContractError('invalid guarded input estimate')
    return estimate * guard['multipliers'][judge] + guard['padding_tokens']


def request_table(identity: dict, candidates: list) -> dict:
    by_id = {r['sample_id']: r for r in candidates}
    table = {}
    for first in identity['first_batches']:
        for phase in ('first', 'repair'):
            batch = {**first, 'phase': phase}
            payload, sha, estimate = previous.payload_record(identity, batch, by_id[batch['ids'][0]])
            table[sha] = {'batch': batch, 'payload_sha256': digest(payload), 'estimate': estimate,
                          'reserve': input_reservation(estimate, batch['judge'], identity['input_guard'])}
    return table


class GuardedBudget(ReviewBudget):
    def __init__(self, workspace: Path, identity: dict, table: dict):
        super().__init__(workspace, identity, identity['budget'])
        self.guard, self.table = identity['input_guard'], table

    def reserve(self, request_digest: str, input_tokens: int) -> int:
        expected = self.table.get(request_digest)
        if expected is None or input_tokens != expected['estimate']:
            raise TopicContractError('unplanned guarded request')
        with self._ledger() as ledger:
            usage = self._usage(ledger)
            if any(r['request_digest'] == request_digest for r in ledger['attempts']):
                raise TopicContractError('duplicate guarded reservation')
            if (ledger.get('halt_reason') or usage['requests'] >= self.policy['max_requests']
                    or usage['charged_input_tokens'] + expected['reserve'] >
                    self.policy['max_input_tokens'] - self.guard['headroom_tokens']
                    or usage['charged_output_tokens'] + self.policy['max_output_tokens'] >
                    self.policy['max_total_output_tokens']):
                raise TopicContractError('review_budget_or_circuit_breaker')
            index = len(ledger['attempts'])
            ledger['attempts'].append({'request_digest': request_digest, 'input_estimate': input_tokens,
                'input_reserve': expected['reserve'], 'output_reserve': self.policy['max_output_tokens']})
            return index

    def settle(self, index: int, usage: dict) -> None:
        if any(type(v) is not int or v < 0 for k, v in usage.items() if k in ('input_tokens', 'output_tokens')):
            raise TopicContractError('invalid guarded provider usage')
        with self._ledger() as ledger:
            row = ledger['attempts'][index]
            for key in ('input_tokens', 'output_tokens'):
                if key in usage:
                    if key in row and row[key] != usage[key]:
                        raise TopicContractError('guarded settlement changed')
                    row[key] = usage[key]
            if (row.get('input_tokens', 0) > row['input_reserve']
                    or row.get('output_tokens', 0) > row['output_reserve']):
                ledger['halt_reason'] = 'provider_usage_exceeded_reservation'

    def halt_reason(self) -> str | None:
        path = self.workspace / 'budget.json'
        return read_json(path).get('halt_reason') if path.exists() else None


class GuardedRequests(previous.Requests):
    """Reuse frozen transport, payload and decoder; replace only accounting."""

    def __init__(self, workspace: Path, identity: dict, base_url: str, auth_file: Path, table: dict):
        super().__init__(workspace, identity, base_url, auth_file)
        self.budget = GuardedBudget(workspace, identity, table)
        # A persisted raw response can precede settlement/circuit persistence.
        ledger = read_json(workspace / 'budget.json') if (workspace / 'budget.json').exists() else {'attempts': []}
        consecutive = 0
        for attempt in ledger['attempts']:
            path = workspace / (attempt['request_digest'] + '.json')
            raw = read_json(path) if path.exists() else {}
            consecutive = consecutive + 1 if raw.get('http_status') in identity['transport_circuit_statuses'] else 0
            if consecutive >= identity['transport_circuit_consecutive_errors']:
                self.state['reason'] = self.state['reason'] or 'repeated_transport_errors'
        self._sync_guard()

    def _sync_guard(self) -> None:
        with self.lock:
            self.state['reason'] = self.state['reason'] or self.budget.halt_reason()
            if self.state['reason']:
                self.circuit.set()
            _atomic_json(self.workspace / 'circuit.json', self.state)

    def call(self, batch: dict, candidate: dict) -> tuple[dict | None, str | None]:
        result = super().call(batch, candidate)
        self._sync_guard()
        return result
