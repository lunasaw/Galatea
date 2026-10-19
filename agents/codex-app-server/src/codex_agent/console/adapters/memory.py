"""One-writer, process-lifetime log. No TTL, eviction, file I/O or sampling."""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import hmac
import json
import re
import secrets

from ..models import EventQuery, ObservationBatch, SnapshotQuery
from ..ports import (CommitConflict, CursorAhead, EpochMismatch, InvalidCursor,
                     PageContextMismatch, SequenceGap, StoreUnavailable)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class MemoryConsoleObservationStore:
    def __init__(self):
        self.epoch = None
        self._closed = False
        self._events, self._changes, self._bindings = [], [], []
        self._batches = {}
        self._bytes = 0
        self._condition = asyncio.Condition()
        self._page_key = secrets.token_bytes(32)

    async def open(self, epoch):
        if self.epoch is not None or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', epoch):
            raise ValueError('store already opened or invalid epoch')
        self.epoch = epoch
        return {'epoch': epoch, 'backend': 'memory', 'retention': 'process_lifetime', 'watermark': self._cursor(0)}

    def _check(self):
        if self.epoch is None or self._closed:
            raise StoreUnavailable('observation store is closed')

    def _cursor(self, seq):
        return f'{self.epoch}:{seq}'

    def _seq(self, cursor, default):
        if cursor is None:
            return default
        if not isinstance(cursor, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}:(0|[1-9][0-9]{0,39})', cursor):
            raise InvalidCursor('expected epoch:decimal-sequence')
        epoch, seq = cursor.split(':')
        if epoch != self.epoch:
            raise EpochMismatch('event history reset')
        value = int(seq)
        if value > len(self._events):
            raise CursorAhead('cursor exceeds committed watermark')
        return value

    @staticmethod
    def _limit(limit):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise InvalidCursor('page size must be between 1 and 1000')

    async def commit(self, batch: ObservationBatch):
        self._check()
        if batch.epoch != self.epoch:
            raise EpochMismatch('batch epoch differs')
        # Complete validation/copying before publishing any mutation. No await inside
        # the critical section; readers cannot see partially applied batches.
        raw = encoded({'events': batch.events, 'changes': batch.changes})
        fingerprint = hashlib.sha256(raw).hexdigest()
        if batch.batch_id in self._batches:
            old, receipt = self._batches[batch.batch_id]
            if old != fingerprint:
                raise CommitConflict('batch id reused with different content')
            return dict(receipt)
        if not batch.batch_id or not batch.events:
            raise ValueError('nonempty batch required')
        start = len(self._events) + 1
        for seq, event in enumerate(batch.events, start):
            if event.get('seq') != str(seq):
                raise SequenceGap('events must continue the committed sequence')
            if event.get('epoch') != self.epoch or event.get('event_id') != self._cursor(seq):
                raise CommitConflict('event identity differs from sequence')
        end = start + len(batch.events) - 1
        for change in batch.changes:
            if not start <= int(change['seq']) <= end:
                raise ValueError('projection outside batch watermark')
            if change.get('type') == 'binding':
                if not change.get('thread_id') or not change.get('session_id'):
                    raise ValueError('binding identity required')
            elif (change.get('view') not in {'messages', 'turns', 'steps'} or not change.get('key')
                  or not isinstance(change.get('set', {}), dict) or not isinstance(change.get('append', {}), dict)):
                raise ValueError('invalid projection change')
            if not (change.get('session_id') or change.get('thread_id')):
                raise ValueError('projection scope required')
        events, changes = copy.deepcopy((batch.events, batch.changes))
        await self._before_publish(batch)
        async with self._condition:
            self._events.extend(events)
            self._changes.extend(changes)
            self._bindings.extend(c for c in changes if c.get('type') == 'binding')
            self._bytes += sum(len(encoded(event)) for event in events)
            receipt = {'watermark': self._cursor(end), 'event_count': len(events)}
            self._batches[batch.batch_id] = (fingerprint, receipt)
            self._condition.notify_all()
        return dict(receipt)

    async def _before_publish(self, batch):
        pass

    def _binding(self, until):
        mapping, version = {}, 0
        for change in self._bindings:
            if int(change['seq']) > until:
                break
            mapping[change['thread_id']] = change['session_id']
            version += 1
        return mapping, version

    @staticmethod
    def _session(row, bindings):
        return row.get('session_id') or bindings.get(row.get('thread_id'))

    async def read_events(self, query: EventQuery):
        self._check()
        self._limit(query.limit)
        after, until = self._seq(query.after, 0), self._seq(query.until, len(self._events))
        if after > until:
            raise InvalidCursor('after exceeds until')
        if query.filters.keys() - {'method', 'kind', 'direction', 'source', 'session_id', 'thread_id', 'turn_id', 'item_id', 'call_id', 'unbound'}:
            raise ValueError('unknown event filter')
        bindings, version = self._binding(until)
        found, scanned = [], after
        for idx in range(after, until):
            row = self._events[idx]
            scanned = idx + 1
            session = self._session(row, bindings)
            if query.session_id and session != query.session_id:
                continue
            if any((session if key == 'session_id' else (session is None) if key == 'unbound' else row.get(key)) != value
                   for key, value in query.filters.items()):
                continue
            row = copy.deepcopy(row)
            if session and not row.get('session_id'):
                row.update(session_id=session, correlation='bound')
            found.append(row)
            if len(found) == query.limit:
                break
        return {'epoch': self.epoch, 'events': found, 'next_cursor': self._cursor(scanned),
                'watermark': self._cursor(until), 'has_more': scanned < until,
                'binding_version': version, 'coverage': {'status': 'complete', 'reason': None}}

    def _token(self, value):
        payload = base64.urlsafe_b64encode(encoded(value)).decode().rstrip('=')
        return payload + '.' + hmac.new(self._page_key, payload.encode(), 'sha256').hexdigest()

    def _decode(self, token):
        try:
            if not isinstance(token, str) or len(token) > 8192:
                raise ValueError()
            payload, signature = token.split('.')
            expected = hmac.new(self._page_key, payload.encode(), 'sha256').hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError()
            return json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        except (ValueError, TypeError, UnicodeError) as exc:
            raise PageContextMismatch('invalid pagination token') from exc

    async def snapshot(self, query: SnapshotQuery):
        self._check()
        self._limit(query.limit)
        if query.view not in {'all', 'messages', 'turns', 'steps'}:
            raise ValueError('unknown view')
        context = {'session': query.session_id, 'turn': query.turn_id, 'view': query.view, 'limit': query.limit}
        before = {}
        until = self._seq(query.until, len(self._events))
        if query.page:
            page = self._decode(query.page)
            if page.get('context') != context or (query.until and query.until != page.get('until')):
                raise PageContextMismatch('pagination context differs')
            until = self._seq(page['until'], 0)
            before = page['before']
        bindings, version = self._binding(until)
        views = {name: {} for name in ('messages', 'turns', 'steps')}
        for replay_order, change in enumerate(self._changes):
            seq = int(change['seq'])
            if seq > until:
                break
            if change.get('type') == 'binding' or self._session(change, bindings) != query.session_id:
                continue
            view = views[change['view']]
            if change.get('set', {}).get('reset_view'):
                view.clear()
                continue
            key = change['key']
            # Aliases reconcile Host-accepted messages and native user items by
            # identity, never by equal text. They are replayed at the same watermark.
            if change.get('remove'):
                view.pop(key, None)
                continue
            row = view.setdefault(key, {'id': key, 'first_seq': str(seq), 'replay_order': replay_order})
            row.update(copy.deepcopy(change.get('set', {})))
            for field, text in change.get('append', {}).items():
                row[field] = row.get(field, '') + text
            row['last_seq'] = str(seq)
        selected = views.keys() if query.view == 'all' else [query.view]
        output, next_before, more = {}, {}, False
        for name in selected:
            rows = [row for row in views[name].values() if not query.turn_id or row.get('turn_id') == query.turn_id]
            rows.sort(key=lambda row: (int(row['first_seq']), row.get('replay_order', 0), row['id']))
            bound = before.get(name, len(rows))
            start = max(0, bound - query.limit)
            output[name] = rows[start:bound]
            next_before[name] = start
            more = more or start > 0
        token = self._token({'context': context, 'until': self._cursor(until), 'before': next_before}) if more else None
        return {**output, 'epoch': self.epoch, 'watermark': self._cursor(until), 'next_page': token,
                'binding_version': version, 'history_start': self._cursor(1 if until else 0),
                'coverage': {'status': 'complete', 'reason': None}}

    async def wait_for_change(self, after, timeout):
        self._check()
        seq = self._seq(after, 0)
        async with self._condition:
            try:
                await asyncio.wait_for(self._condition.wait_for(lambda: self._closed or len(self._events) > seq), timeout)
            except TimeoutError:
                pass
            self._check()
            return self._cursor(len(self._events))

    async def stats(self):
        self._check()
        return {'epoch': self.epoch, 'watermark': self._cursor(len(self._events)), 'event_count': len(self._events),
                'encoded_bytes': self._bytes, 'binding_version': len(self._bindings),
                'view_count': len({(c.get('session_id'), c.get('thread_id'), c.get('view'), c.get('key'))
                                   for c in self._changes if c.get('view')}),
                'backend': 'memory', 'retention': 'process_lifetime'}

    async def close(self):
        async with self._condition:
            self._closed = True
            self._condition.notify_all()
