"""Persist API request reservations before dispatch, including interrupted calls."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path

from ._common import digest
from .fact_review_server import _atomic_json
from .topic_candidates import read_json
from .topic_context import TopicContractError


class ReviewBudget:
    def __init__(self, workspace: Path, identity: dict, policy: dict):
        self.workspace = workspace
        self.identity_sha256 = digest(identity)
        self.policy = policy

    @contextmanager
    def _ledger(self):
        path = self.workspace / 'budget.json'
        with (self.workspace / 'budget.lock').open('a') as lock:
            (self.workspace / 'budget.lock').chmod(0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            ledger = read_json(path) if path.exists() else {
                'identity_sha256': self.identity_sha256, 'attempts': []}
            if ledger['identity_sha256'] != self.identity_sha256:
                raise TopicContractError('review budget identity mismatch')
            yield ledger
            _atomic_json(path, ledger)

    @staticmethod
    def _usage(ledger: dict) -> dict:
        attempts = ledger['attempts']
        return {
            'requests': len(attempts),
            'reserved_input_tokens': sum(row['input_reserve'] for row in attempts),
            'input_tokens': sum(row.get('input_tokens', 0) for row in attempts),
            'output_tokens': sum(row.get('output_tokens', 0) for row in attempts),
            'charged_input_tokens': sum(max(row['input_reserve'], row.get('input_tokens', 0)) for row in attempts),
            'charged_output_tokens': sum(max(row['output_reserve'], row.get('output_tokens', 0)) for row in attempts),
            'requests_without_reported_usage': sum('input_tokens' not in row for row in attempts),
        }

    def usage(self) -> dict:
        with self._ledger() as ledger:
            return self._usage(ledger)

    def reserve(self, request_digest: str, input_tokens: int) -> int:
        with self._ledger() as ledger:
            usage = self._usage(ledger)
            if (usage['requests'] >= self.policy['max_requests']
                    or usage['charged_input_tokens'] + input_tokens > self.policy['max_input_tokens']
                    or usage['charged_output_tokens'] + self.policy['max_output_tokens'] > self.policy['max_total_output_tokens']):
                raise TopicContractError('review_budget_or_circuit_breaker')
            index = len(ledger['attempts'])
            ledger['attempts'].append({'request_digest': request_digest, 'input_reserve': input_tokens,
                                       'output_reserve': self.policy['max_output_tokens']})
            return index

    def settle(self, index: int, usage: dict) -> None:
        with self._ledger() as ledger:
            row = ledger['attempts'][index]
            for key in ('input_tokens', 'output_tokens'):
                if key in usage:
                    if type(usage[key]) is not int or usage[key] < 0:
                        raise TopicContractError('invalid provider token usage')
                    row[key] = usage[key]
