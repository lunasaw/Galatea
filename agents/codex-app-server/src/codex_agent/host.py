from __future__ import annotations

import asyncio
import inspect
import json
import threading
import time
import uuid

from .registry import ToolContext, ToolRegistry
from jsonschema import Draft202012Validator
from .state import AtomicJsonStore, identifier, payload_digest, single_writer_lock


class HostNotReady(RuntimeError):
    pass


class AgentHost:
    """Durable admission and observation around the native app-server loop."""

    def __init__(self, *, registry: ToolRegistry, event_log, store: AtomicJsonStore,
                 client_factory, reconciler, principal_id: str, project_ids: set[str],
                 campaign_ids: set[str], actions: set[str], max_active_turns: int = 4,
                 workspace_dir=None, bindings=None, tool_timeout=60.0, client_stop=None, admission_check=None, startup_check=None):
        self.registry, self.events, self.store = registry, event_log, store
        self.client_factory, self.reconciler, self.client_stop = client_factory, reconciler, client_stop
        self.principal_id, self.project_ids = principal_id, frozenset(project_ids)
        self.campaign_ids, self.actions = frozenset(campaign_ids), frozenset(actions)
        self.max_active_turns, self.tool_timeout = max_active_turns, tool_timeout
        self.workspace_dir = workspace_dir or store.root
        self.admission_check, self.startup_check = admission_check, startup_check
        self.bindings = {**(bindings or {}), 'catalog_digest': registry.catalog.digest,
                         'principal_digest': payload_digest([principal_id, sorted(project_ids), sorted(campaign_ids), sorted(actions)])}
        self.client, self.ready = None, False
        self._sessions, self._attached, self._cancels = {}, set(), {}
        self._admission = asyncio.Lock()
        self._active_turns = 0
        self._reconciler_last_success = 0.0
        self._reconcile_task, self._writer = None, None
        self._workers = set()

    async def _reconcile(self):
        if inspect.iscoroutinefunction(self.reconciler):
            await asyncio.wait_for(self.reconciler(), 30)
        else:
            worker = asyncio.create_task(asyncio.to_thread(self.reconciler))
            self._workers.add(worker)
            worker.add_done_callback(self._workers.discard)
            await asyncio.wait_for(asyncio.shield(worker), 30)
        self._reconciler_last_success = time.monotonic()

    async def start(self):
        self._writer = single_writer_lock(self.store.root / '.host.lock')
        self._writer.__enter__()
        try:
            if self.startup_check:
                await self.startup_check()
            await self._reconcile()
            self.client = self.client_factory()
            if inspect.isawaitable(self.client):
                self.client = await self.client
            # A persisted active turn is an observation problem, never a new submission.
            for session in self.list_sessions():
                if session['status'] != 'closed':
                    session['status'] = 'recovery_required'
                    self._save(session)
            self.ready = True
            self._reconcile_task = asyncio.create_task(self._reconcile_loop())
        except BaseException:
            await self.stop()
            raise

    async def _reconcile_loop(self):
        while self.ready:
            await asyncio.sleep(15)
            try:
                await self._reconcile()
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                self.ready = False
                return
            except Exception:
                # Freshness remains stale until an actual successful reconciliation.
                continue

    async def stop(self):
        self.ready = False
        for cancel in self._cancels.values():
            cancel.set()
        if self._reconcile_task:
            self._reconcile_task.cancel()
            await asyncio.gather(self._reconcile_task, return_exceptions=True)
            self._reconcile_task = None
        close = self.client_stop or getattr(self.client, 'stop', None)
        if close:
            result = close()
            if inspect.isawaitable(result):
                await result
        # Worker threads cannot be killed safely; keep the writer lock until they exit.
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        if self._writer:
            self._writer.__exit__(None, None, None)
            self._writer = None

    def assert_ready(self, max_age=60.0):
        peer = getattr(self.client, 'peer', None)
        if (not self.ready or time.monotonic() - self._reconciler_last_success > max_age
                or (self._reconcile_task and self._reconcile_task.done())
                or (peer and not peer.healthy)):
            raise HostNotReady('runtime or reconciler unavailable')

    def _save(self, session):
        self.store.write(f"sessions/{session['id']}.json", session)
        self._sessions[session['id']] = session

    def _session(self, session_id):
        identifier(session_id)
        session = self._sessions.get(session_id) or self.store.read(f'sessions/{session_id}.json')
        if session:
            if session.get('id') != session_id or session.get('principal_id') != self.principal_id:
                raise KeyError('unknown session')
            self._sessions[session_id] = session
        return session

    def _session_for_thread(self, thread_id):
        return next((s for s in self._sessions.values() if s['thread_id'] == thread_id), None)

    async def _check_admission(self):
        self.assert_ready()
        if self.admission_check:
            try:
                await self.admission_check()
            except Exception:
                await self.runtime_failed(None)
                raise HostNotReady('effective runtime configuration changed')

    async def create_session(self, session_id=None):
        self.assert_ready()
        session_id = identifier(session_id or 'session-' + uuid.uuid4().hex)
        async with self._admission:
            existing = self._session(session_id)
            if existing:
                return dict(existing)
            await self._check_admission()
            intent = f'session-intents/{session_id}.json'
            if self.store.read(intent):
                raise HostNotReady('thread creation outcome unknown')
            self.store.write(intent, {'id': session_id, 'status': 'intent_recorded'})
            result = await self.client.start_thread(cwd=str(self.workspace_dir), dynamic_tools=self.registry.dynamic_tools(self.actions))
            session = {'id': session_id, 'thread_id': result['thread']['id'], 'principal_id': self.principal_id,
                       'status': 'ready', **self.bindings, 'active_turn_id': None, 'active_request_id': None}
            self._save(session)
            self._attached.add(session_id)
            self.events.append(session_id, {'kind': 'session', 'public': {'status': 'ready'}})
            return dict(session)

    async def send_message(self, session_id, request_id, text):
        identifier(request_id)
        if not isinstance(text, str) or not text.strip() or len(text.encode()) > 256 * 1024:
            raise ValueError('invalid message')
        digest = payload_digest({'session_id': session_id, 'text': text})
        async with self._admission:
            session = self._session(session_id)
            if not session:
                raise KeyError(session_id)
            path = f'requests/{request_id}.json'
            existing = self.store.read(path)
            if existing:
                if existing.get('payload_digest') != digest:
                    raise ValueError('request id conflict')
                if existing['status'] == 'intent_recorded':
                    existing['status'] = 'unknown'
                    self.store.write(path, existing)
                return existing
            await self._check_admission()
            if session_id not in self._attached or session['status'] != 'ready' or self._active_turns >= self.max_active_turns:
                raise HostNotReady('session busy or recovery required')
            record = {'request_id': request_id, 'session_id': session_id, 'thread_id': session['thread_id'],
                      'payload_digest': digest, 'status': 'intent_recorded'}
            self.store.write(path, record)
            session.update(status='starting', active_request_id=request_id, active_turn_id=None)
            self._save(session)
            self._active_turns += 1
            try:
                result = await self.client.start_turn(session['thread_id'], text)
                turn_id = result['turn']['id']
                if session.get('active_turn_id') not in (None, turn_id):
                    raise HostNotReady('native turn mismatch')
                # Notifications may precede the RPC response on the wire.
                latest = self.store.read(path)
                if latest['status'] not in {'completed', 'failed', 'interrupted'}:
                    record.update(turn_id=turn_id, status='accepted')
                    self.store.write(path, record)
                    session.update(status='running', active_turn_id=turn_id)
                    self._save(session)
                    self.events.append(session_id, {'kind': 'turn', 'turn_id': turn_id, 'public': {'status': 'accepted'}})
                    return record
                return latest
            except BaseException:
                record.update(status='unknown')
                self.store.write(path, record)
                session['status'] = 'recovery_required'
                self._save(session)
                raise

    async def handle_tool_call(self, message):
        if message.get('method', 'item/tool/call') != 'item/tool/call':
            return {'error': {'code': -32601, 'message': 'Unsupported server request'}}
        params = message.get('params', {})
        session = self._session_for_thread(params.get('threadId'))
        call_id = params.get('callId', '')
        try:
            self.assert_ready()
            identifier(call_id)
            if (not session or session['id'] not in self._attached or params.get('namespace') != 'galatea'
                    or session['status'] != 'running' or params.get('turnId') != session.get('active_turn_id')):
                raise HostNotReady('unbound tool call')
        except (HostNotReady, ValueError):
            envelope = self.registry._error('req-' + uuid.uuid4().hex, 'forbidden', 'reconcile')
        else:
            # Native call IDs are scoped to the session, not used as filesystem paths.
            journal_id = payload_digest([session['id'], call_id])
            cancel = self._cancels.setdefault(session['id'], threading.Event())
            context = ToolContext(session['id'], params['threadId'], params['turnId'], journal_id,
                                  self.principal_id, self.project_ids, self.campaign_ids, self.actions,
                                  session['catalog_digest'], payload_digest(self.bindings),
                                  time.monotonic() + self.tool_timeout, cancel)
            name, arguments = params.get('tool', ''), params.get('arguments', {})
            metadata = self.registry.catalog.metadata['tools'].get(name, {})
            public_args = {}
            schema = self.registry.catalog.schemas.get(name)
            if schema and Draft202012Validator(schema).is_valid(arguments):
                public_args = {key: arguments[key] for key in metadata.get('public_fields', []) if key in arguments}
            started = time.monotonic()
            self.events.append(session['id'], {'kind': 'tool_call', 'turn_id': params['turnId'],
                               'public': {'tool': name, 'call_id': call_id, 'status': 'executing',
                                          'arguments': public_args, 'read_only': metadata.get('read_only')}})
            worker = asyncio.create_task(asyncio.to_thread(self.registry.call, context, params.get('tool', ''), params.get('arguments', {})))
            self._workers.add(worker)
            worker.add_done_callback(self._workers.discard)
            try:
                envelope = await asyncio.wait_for(asyncio.shield(worker), self.tool_timeout)
            except TimeoutError:
                cancel.set()
                session['status'] = 'recovery_required'
                self._save(session)
                envelope = self.registry._error('req-' + uuid.uuid4().hex, 'tool-timeout', 'reconcile', state_changed='unknown')
            if envelope.get('error', {}).get('state_changed') == 'unknown':
                session['status'] = 'recovery_required'
                self._save(session)
            self.events.append(session['id'], {'kind': 'tool_result', 'turn_id': params['turnId'],
                'public': {'tool': params.get('tool'), 'call_id': call_id, 'ok': envelope['ok'],
                           'elapsed_ms': round((time.monotonic() - started) * 1000),
                           'read_only': metadata.get('read_only'), 'arguments': public_args,
                           'category': envelope.get('error', {}).get('category'),
                           'operation_id': envelope.get('data', {}).get('operation_id') if isinstance(envelope.get('data'), dict) else None}})
        return {'result': {'success': envelope['ok'], 'contentItems': [{'type': 'inputText', 'text': json.dumps(envelope, ensure_ascii=False)}]}}

    async def handle_notification(self, message):
        method, params = message.get('method'), message.get('params', {})
        session = self._session_for_thread(params.get('threadId'))
        if not session or session['id'] not in self._attached:
            return
        turn = params.get('turn', {})
        turn_id = turn.get('id', params.get('turnId'))
        if method == 'turn/started' and session['status'] == 'starting':
            session.update(status='running', active_turn_id=turn_id)
            self._save(session)
        elif method == 'turn/completed':
            if turn_id != session.get('active_turn_id'):
                return
            path = f"requests/{session['active_request_id']}.json"
            record = self.store.read(path)
            record.update(turn_id=turn_id, status=turn.get('status', 'failed'))
            self.store.write(path, record)
            self._active_turns = max(0, self._active_turns - 1)
            status = 'closed' if session['status'] == 'closing' else 'ready'
            if session['status'] == 'recovery_required':
                status = 'recovery_required'
            session.update(status=status, active_turn_id=None, active_request_id=None)
            self._save(session)
            self._cancels.pop(session['id'], None)
        elif method not in {'item/started', 'item/completed', 'item/agentMessage/delta', 'thread/tokenUsage/updated'}:
            return
        # Native item content can contain prompts, reasoning, samples, or logs.
        # Console projects only lifecycle metadata; full rollout stays in CODEX_HOME.
        item = params.get('item', {})
        self.events.append(session['id'], {'kind': 'native', 'turn_id': turn_id, 'item_id': params.get('itemId', item.get('id')),
            'public': {'method': method, 'status': turn.get('status'), 'item_type': item.get('type')}})

    async def runtime_failed(self, exc):
        self.ready = False
        for session_id in self._attached:
            session = self._sessions[session_id]
            if session['status'] != 'closed':
                session['status'] = 'recovery_required'
                self._save(session)
        self._attached.clear()

    async def interrupt(self, session_id):
        async with self._admission:
            return await self._interrupt(session_id)

    async def _interrupt(self, session_id):
        session = self._session(session_id)
        if not session:
            raise KeyError(session_id)
        if session.get('active_turn_id'):
            self._cancels.setdefault(session_id, threading.Event()).set()
            if session['status'] != 'closing':
                session['status'] = 'interrupting'
            self._save(session)
            await self.client.interrupt(session['thread_id'], session['active_turn_id'])
        self.events.append(session_id, {'kind': 'turn', 'public': {'status': 'pending', 'action': 'interrupt'}})
        return {'status': 'pending' if session.get('active_turn_id') else session['status']}

    async def resume(self, session_id):
        self.assert_ready()
        async with self._admission:
            session = self._session(session_id)
            if not session:
                raise KeyError(session_id)
            persisted = self.store.read(f'sessions/{session_id}.json')
            if persisted != session or any(session.get(k) != v for k, v in self.bindings.items()) or session['status'] == 'closed':
                raise HostNotReady('session binding mismatch')
            if session['id'] in self._attached and session.get('active_turn_id'):
                raise HostNotReady('turn still active')
            for path in (self.store.root / 'tool-calls').glob('*.json'):
                call = self.store.read(str(path.relative_to(self.store.root)))
                if call['identity']['session_id'] == session_id and call['state'] != 'receipt_saved':
                    raise HostNotReady('tool outcome unknown; reconcile operation before resume')
            await self._check_admission()
            observed = await self.client.read_thread(session['thread_id'])
            thread = observed['thread']
            if thread['id'] != session['thread_id'] or any(t.get('status') == 'inProgress' for t in thread.get('turns', [])):
                raise HostNotReady('native thread still active')
            if session.get('active_request_id'):
                turn = next((t for t in thread.get('turns', []) if t['id'] == session.get('active_turn_id')), None)
                if not turn or turn.get('status') not in {'completed', 'failed', 'interrupted'}:
                    raise HostNotReady('turn outcome unknown')
                path = f"requests/{session['active_request_id']}.json"
                record = self.store.read(path)
                record.update(turn_id=turn['id'], status=turn['status'])
                self.store.write(path, record)
            await self.client.resume(session['thread_id'])
            session.update(status='ready', active_turn_id=None, active_request_id=None)
            self._save(session)
            self._attached.add(session_id)
            self._cancels.pop(session_id, None)
            self._active_turns = sum(bool(self._sessions[s].get('active_turn_id')) for s in self._attached)
            self.events.append(session_id, {'kind': 'session', 'public': {'status': 'resumed'}})
            return dict(session)

    def get_session(self, session_id):
        session = self._session(session_id)
        return dict(session) if session else None

    async def close_session(self, session_id):
        async with self._admission:
            return await self._close_session(session_id)

    async def _close_session(self, session_id):
        session = self._session(session_id)
        if not session:
            raise KeyError(session_id)
        if session['status'] == 'recovery_required':
            raise HostNotReady('reconcile before closing')
        if session.get('active_turn_id'):
            session['status'] = 'closing'
            self._save(session)
            await self._interrupt(session_id)
        else:
            session['status'] = 'closed'
            self._save(session)
        self.events.append(session_id, {'kind': 'session', 'public': {'status': session['status']}})
        return dict(session)

    def list_sessions(self):
        result = []
        for path in sorted((self.store.root / 'sessions').glob('*.json')):
            try:
                result.append(dict(self._session(path.stem)))
            except KeyError:
                continue
        return result
