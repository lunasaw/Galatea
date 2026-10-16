"""Ordered observation writer and queries, independent of execution state."""
import asyncio
import uuid

from .adapters.memory import MemoryConsoleObservationStore
from .correlation import Correlator
from .models import EventQuery, ObservationBatch
from .observer import ConsoleObserver
from .projection import Projector, redact
from .ports import StoreUnavailable, InvalidCursor, CursorAhead, UnknownEvent


CAPABILITIES = {
    'console_schema_version': 'galatea.console/v1', 'console_role': 'training_agent_chat_trace',
    'storage': {'backend': 'memory', 'retention': 'process_lifetime'},
    'skill_integration': True, 'inference_endpoint': False, 'native_codex_comparison': 'versioned_protocol',
    'principal_scope': 'host_configured', 'network_access': 'trusted_network_required', 'interactive_requests': True,
    'conversation_storage': 'native_rollout', 'native_protocol_version': 'codex-cli 0.154.0',
}


class ConsoleObservationService:
    def __init__(self, store=None, *, epoch=None):
        self.store = store if store is not None else MemoryConsoleObservationStore()
        self.epoch = epoch or uuid.uuid4().hex
        self.observer = ConsoleObserver()
        self.correlator, self.projector = Correlator(), Projector()
        self.worker = None
        self._seq = 0
        self._batch = None
        self._error = None
        self._incomplete = None
        self._retry = asyncio.Event()
        self._outbound_ids = {}
        self.runtime_instance_id = None

    async def start(self):
        if self.worker is None:
            await self.store.open(self.epoch)
            self.epoch = self.store.epoch
            self._seq = int((await self.store.stats())['watermark'].split(':')[1])
            self.observer.loop = asyncio.get_running_loop()
            self.worker = asyncio.create_task(self._write(), name='console-observation-writer')

    def append(self, session_id, event):
        context = {key: event.get(key) for key in ('thread_id', 'turn_id', 'item_id', 'call_id', 'host_request_id')}
        context['session_id'] = session_id
        self.observer(None, event, source='host', kind=event.get('kind', 'session_state'), context=context,
                      runtime_instance_id=self.runtime_instance_id)

    def bind(self, session_id, thread_id):
        self.append(session_id, {'kind': 'binding', 'thread_id': thread_id})

    def diagnostic(self, kind, *, runtime_instance_id=None, **detail):
        if kind == 'observation_error':
            self._incomplete = 'capture_failed'
        self.observer(None, detail, source='host', kind=kind, runtime_instance_id=runtime_instance_id)

    def _event(self, entry):
        try:
            row, bindings = self.correlator.process(entry)
        except Exception as exc:
            self._incomplete = type(exc).__name__
            # A failed association must not discard an otherwise captured message
            # or assign it to a recently active session.
            row = {key: entry[key] for key in ('source', 'direction', 'runtime_instance_id', 'observed_at')}
            row.update({key: entry['context'].get(key) for key in
                        ('session_id', 'host_request_id', 'thread_id', 'turn_id', 'item_id', 'call_id')})
            row.update(kind=entry['kind'] or 'correlation_failed', method=entry['payload'].get('method'),
                       rpc_id=entry['payload'].get('id'), correlation='unavailable')
            bindings = []
        self._seq += 1
        row.update(schema_version='galatea.console.event/v1', epoch=self.epoch,
                   seq=str(self._seq), event_id=f'{self.epoch}:{self._seq}')
        if entry['direction'] == 'host_to_runtime':
            self._outbound_ids[entry['capture_id']] = row['event_id']
        if entry['kind'] == 'send_failed' and entry['payload'].get('outbound_capture_id'):
            entry['payload']['outbound_event_id'] = self._outbound_ids.get(entry['payload']['outbound_capture_id'])
        if row['runtime_instance_id']:
            self.runtime_instance_id = row['runtime_instance_id']
        try:
            row['payload'], row['redactions'] = redact(entry['payload'], method=row['method'])
        except Exception as exc:
            self._incomplete = type(exc).__name__
            row.update(payload={'content_unavailable': 'projection_failed'},
                       redactions=[{'path': '', 'reason': 'content_unavailable'}])
        try:
            changes = self.projector.changes(row)
        except Exception as exc:
            self._incomplete = type(exc).__name__
            row['projection_status'] = 'unavailable'
            changes = []
        events = [row]
        for binding in bindings:
            self._seq += 1
            fact = {**row, **binding, 'seq': str(self._seq), 'event_id': f'{self.epoch}:{self._seq}',
                    'source': 'host', 'direction': None, 'kind': 'binding_changed', 'method': None,
                    'rpc_id': None, 'payload': binding, 'redactions': []}
            events.append(fact)
            changes.append({'type': 'binding', 'seq': fact['seq'], **binding})
        return ObservationBatch(row['event_id'], self.epoch, tuple(events), tuple(changes))

    async def _write(self):
        while True:
            entry = await self.observer.queue.get()
            self._batch = self._event(entry)
            delay = .05
            while True:
                try:
                    await self.store.commit(self._batch)
                    self._error = None
                    break
                except Exception as exc:
                    self._error = type(exc).__name__
                    self._retry.clear()
                    try:
                        await asyncio.wait_for(self._retry.wait(), delay)
                    except TimeoutError:
                        pass
                    delay = min(delay * 2, 5)
            self._batch = None
            self.observer.queue.task_done()
            # A sustained backlog must still leave the RPC reader runnable.
            await asyncio.sleep(0)

    async def retry(self):
        self._retry.set()

    async def flush(self):
        await self.start()
        if self._error and not self._retry.is_set():
            raise StoreUnavailable(self._error)
        await asyncio.wait_for(self.observer.queue.join(), 10)

    async def read_events(self, query):
        return self._coverage(await self.store.read_events(query))

    async def snapshot(self, query):
        return self._coverage(await self.store.snapshot(query))

    async def wait_for_change(self, after, timeout):
        return await self.store.wait_for_change(after, timeout)

    async def detail(self, event_id):
        # Validate through the adapter before deriving a preceding cursor.
        try:
            await self.store.read_events(EventQuery(after=event_id, limit=1))
        except CursorAhead as exc:
            raise UnknownEvent('event not committed in this epoch') from exc
        seq = int(event_id.split(':')[1])
        if seq == 0:
            raise UnknownEvent('zero is a cursor, not an event')
        page = await self.read_events(EventQuery(after=f'{self.epoch}:{seq - 1}', until=event_id, limit=1))
        return page['events'][0]

    def _coverage(self, result):
        error = self.observer.error or self._incomplete or self._error
        if error:
            result['coverage'] = {'status': 'incomplete', 'reason': error}
        return result

    async def status(self):
        stats = await self.store.stats()
        pending = self.observer.queue.qsize() + (1 if self._batch else 0)
        error = self._error or self.observer.error or self._incomplete
        status = 'unavailable' if self._error else 'incomplete' if error else 'lagging' if pending else 'ready'
        return {**CAPABILITIES, 'storage': {'backend': stats['backend'], 'retention': stats['retention']},
                'epoch': self.epoch, 'runtime_instance_id': self.runtime_instance_id,
                'observation': {'status': status, 'captured_seq': str(self._seq + self.observer.queue.qsize()),
                    'committed_seq': stats['watermark'].split(':')[1], 'pending_events': pending,
                    'event_count': stats['event_count'], 'encoded_bytes': stats['encoded_bytes'], 'error': error}}

    async def close(self):
        if self.worker is not None:
            if not self._error:
                await self.flush()
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        await self.store.close()
