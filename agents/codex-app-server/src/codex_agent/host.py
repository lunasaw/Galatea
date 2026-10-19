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
from .console.observer import observation_context
from .native import NativeBridge, PROTOCOL
from .mcp_app_context import with_app_context


class HostNotReady(RuntimeError):
    pass


class AgentHost:
    """Durable admission and observation around the native app-server loop."""

    def __init__(self, *, registry: ToolRegistry, event_log, store: AtomicJsonStore,
                 client_factory, reconciler, principal_id: str, actions: set[str], max_active_turns: int = 4,
                 workspace_dir=None, bindings=None, tool_timeout=60.0, client_stop=None, admission_check=None, startup_check=None,
                 configuration_changed=None, extensions_close=None):
        self.registry, self.events, self.store = registry, event_log, store
        self.client_factory, self.reconciler, self.client_stop = client_factory, reconciler, client_stop
        self.principal_id, self.actions = principal_id, frozenset(actions)
        self.max_active_turns, self.tool_timeout = max_active_turns, tool_timeout
        self.workspace_dir = workspace_dir or store.root
        self.admission_check, self.startup_check = admission_check, startup_check
        self.bindings = {**(bindings or {}), 'catalog_digest': registry.catalog.digest,
                         'principal_digest': payload_digest([principal_id, sorted(actions)])}
        self.client, self.ready = None, False
        self._sessions, self._attached, self._cancels = {}, set(), {}
        self._admission = asyncio.Lock()
        self._active_turns = 0
        self._reconciler_last_success = 0.0
        self._reconcile_task, self._writer = None, None
        self._workers = set()
        self.configuration_changed = configuration_changed
        self.extensions_close = extensions_close
        self.native = NativeBridge(self)

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
        await self.events.start()
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
                if (self.configuration_changed and not session.get('active_turn_id') and
                        all(session.get(key) == self.bindings.get(key) for key in
                            ('catalog_digest', 'principal_digest', 'runtime_binary_sha256', 'runtime_compatibility_sha256', 'extensions_sha256'))):
                    session.update({key: self.bindings[key] for key in ('effective_config_sha256', 'agent_config_sha256') if key in self.bindings})
                self.events.bind(session['id'], session['thread_id'])
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
        self.native.cancel()
        for cancel in self._cancels.values():
            cancel.set()
        if self._reconcile_task:
            self._reconcile_task.cancel()
            await asyncio.gather(self._reconcile_task, return_exceptions=True)
            self._reconcile_task = None
        try:
            close = self.client_stop or getattr(self.client, 'stop', None)
            if close:
                result = close()
                if inspect.isawaitable(result):
                    await result
        finally:
            try:
                # Keep providers and the writer lock alive until workers exit.
                if self._workers:
                    await asyncio.gather(*self._workers, return_exceptions=True)
                if self.extensions_close:
                    await asyncio.to_thread(self.extensions_close)
            finally:
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

    def _count_active_turns(self):
        # A native acknowledgement can precede turn/started. Reservations still
        # consume a slot while their turn identity is unknown.
        return sum(bool(s.get('active_turn_id') or s.get('active_request_id'))
                   for key in self._attached for s in [self._sessions[key]])

    def mark_deleted(self, session):
        session.update(deleted=True, archived=True, status='closed', active_turn_id=None, active_request_id=None)
        self.native.cancel(session['thread_id'])
        self._attached.discard(session['id'])
        self._active_turns = self._count_active_turns()
        self._save(session)
        self.events.append(session['id'], {'kind': 'session', 'public': {'status': 'deleted'}})

    async def _check_admission(self):
        self.assert_ready()
        if self.admission_check:
            try:
                await self.admission_check()
            except Exception:
                await self.runtime_failed(None)
                raise HostNotReady('effective runtime configuration changed')

    async def create_session(self, session_id=None, *, options=None):
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
            settings = {'cwd': str(self.workspace_dir), **(options or {})}
            PROTOCOL.validate(PROTOCOL.schema('thread/start'), settings)
            settings.pop('dynamicTools', None)
            with observation_context(session_id=session_id):
                result = await self.client.start_thread(**settings, dynamic_tools=self.registry.dynamic_tools(self.actions))
            if self.configuration_changed:
                # Native thread creation records workspace trust in user config.
                await self.configuration_changed()
            # Keep the effective native settings, including inherited defaults,
            # so the console does not display a different permission or model.
            settings.update({key: result[key] for key in ('model', 'modelProvider', 'approvalPolicy',
                            'approvalsReviewer', 'permissions', 'cwd') if key in result})
            if isinstance(result.get('sandbox'), dict):
                settings['sandboxPolicy'] = result['sandbox']
                settings.pop('sandbox', None)
            if 'reasoningEffort' in result:
                settings['effort'] = result['reasoningEffort']
            session = {'id': session_id, 'thread_id': result['thread']['id'], 'principal_id': self.principal_id,
                       'status': 'ready', **self.bindings, 'active_turn_id': None, 'active_request_id': None,
                       'settings': settings, 'cwd': result.get('cwd', settings['cwd'])}
            self._save(session)
            self._attached.add(session_id)
            self.events.bind(session_id, session['thread_id'])
            self.events.append(session_id, {'kind': 'session', 'public': {'status': 'ready'}})
            return dict(session)

    async def send_message(self, session_id, request_id, text, *, input_items=None, options=None):
        identifier(request_id)
        if not isinstance(text, str) or (not text.strip() and not input_items) or len(text.encode()) > 256 * 1024:
            raise ValueError('invalid message')
        if input_items is not None or options:
            if options and set(options) & {'threadId', 'input'}:
                raise ValueError('thread identity and input are managed by Host')
            PROTOCOL.validate(PROTOCOL.schema('turn/start'), {'threadId': 'validation', **(options or {}), 'input': input_items or
                              [{'type': 'text', 'text': text, 'text_elements': []}]})
        digest = payload_digest({'session_id': session_id, 'text': text, 'input': input_items, 'options': options})
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
            options = with_app_context(self.store, session_id, options)
            record = {'request_id': request_id, 'session_id': session_id, 'thread_id': session['thread_id'],
                      'payload_digest': digest, 'status': 'intent_recorded'}
            self.store.write(path, record)
            session.update(status='starting', active_request_id=request_id, active_turn_id=None)
            self._save(session)
            self._active_turns += 1
            self.events.append(session_id, {'kind': 'message_accepted', 'thread_id': session['thread_id'],
                               'host_request_id': request_id, 'public': {'text': text, 'content': input_items}})
            try:
                with observation_context(session_id=session_id, thread_id=session['thread_id'], host_request_id=request_id):
                    kwargs = {'input_items': input_items, 'options': options} if input_items is not None or options else {}
                    result = await self.client.start_turn(session['thread_id'], text, **kwargs)
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
            return await self.native.request(message)
        params = message.get('params', {})
        session = self._session_for_thread(params.get('threadId'))
        call_id = params.get('callId', '')
        try:
            self.assert_ready()
            identifier(call_id)
            if (not session or session['id'] not in self._attached or params.get('namespace') != self.registry.catalog.namespace
                    or session['status'] != 'running' or params.get('turnId') != session.get('active_turn_id')):
                raise HostNotReady('unbound tool call')
        except (HostNotReady, ValueError):
            envelope = self.registry._error('req-' + uuid.uuid4().hex, 'forbidden', 'reconcile')
        else:
            # Native call IDs are scoped to the session, not used as filesystem paths.
            journal_id = payload_digest([session['id'], call_id])
            cancel = self._cancels.setdefault(session['id'], threading.Event())
            context = ToolContext(session['id'], params['threadId'], params['turnId'], journal_id,
                                  self.principal_id, self.actions,
                                  session['catalog_digest'], payload_digest(self.bindings),
                                  time.monotonic() + self.tool_timeout, cancel)
            name, arguments = params.get('tool', ''), params.get('arguments', {})
            metadata = self.registry.catalog.metadata['tools'].get(name, {})
            public_args = {}
            schema = self.registry.catalog.schemas.get(name)
            if schema and Draft202012Validator(schema).is_valid(arguments):
                public_args = {key: arguments[key] for key in metadata.get('public_fields', []) if key in arguments}
            started = time.monotonic()
            self.events.append(session['id'], {'kind': 'tool_call', 'thread_id': session['thread_id'], 'turn_id': params['turnId'],
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
            self.events.append(session['id'], {'kind': 'tool_result', 'thread_id': session['thread_id'], 'turn_id': params['turnId'],
                'public': {'tool': params.get('tool'), 'call_id': call_id, 'ok': envelope['ok'],
                           'elapsed_ms': round((time.monotonic() - started) * 1000),
                           'read_only': metadata.get('read_only'), 'arguments': public_args,
                           'category': envelope.get('error', {}).get('category'),
                           'operation_id': envelope.get('data', {}).get('operation_id') if isinstance(envelope.get('data'), dict) else None}})
        return {'result': {'success': envelope['ok'], 'contentItems': [{'type': 'inputText', 'text': json.dumps(envelope, ensure_ascii=False)}]}}

    async def handle_notification(self, message):
        method, params = message.get('method'), message.get('params', {})
        if method == 'account/gatewayOAuth/changed':
            self.native.gateway_changed(params)
            return
        if method == 'serverRequest/resolved':
            for row, future in self.native.pending.values():
                if row['rpc_id'] == params.get('requestId') and not future.done():
                    future.cancel()
            return
        session = self._session_for_thread(params.get('threadId'))
        if session and method == 'thread/name/updated':
            session['name'] = params.get('threadName')
            self._save(session)
            return
        if session and method in {'thread/readState/changed', 'thread/prediction/updated',
                                   'thread/environment/connected', 'thread/environment/disconnected',
                                   'thread/project/updated', 'thread/status/changed'}:
            if method == 'thread/readState/changed':
                session['read_state'] = params.get('readState')
            elif method == 'thread/prediction/updated':
                session['prediction'] = params
            elif method == 'thread/project/updated':
                session['settings'] = {**session.get('settings', {}), 'projectId': params.get('projectId') or ''}
            elif method == 'thread/status/changed':
                session['native_status'] = params.get('status')
            else:
                session['environment_status'] = {'connected': method.endswith('/connected'), **params}
            self._save(session)
            return
        if session and method == 'thread/settings/updated':
            updated = params.get('threadSettings', {})
            settings = dict(session.get('settings', {}))
            settings.update({key: updated[key] for key in ('model', 'modelProvider', 'effort', 'summary',
                'serviceTier', 'personality', 'approvalPolicy', 'approvalsReviewer', 'sandboxPolicy', 'cwd')
                if key in updated})
            if 'activePermissionProfile' in updated:
                profile = updated['activePermissionProfile'] or {}
                if profile.get('id'):
                    settings['permissions'] = profile['id']
                else:
                    settings.pop('permissions', None)
            if 'collaborationMode' in updated:
                mode = updated['collaborationMode']
                values = mode.get('settings', {})
                settings['collaborationMode'] = {'mode': mode['mode'], 'settings': {
                    'model': values.get('model'), 'reasoning_effort': values.get('reasoning_effort'),
                    'developer_instructions': None}}
            if 'sandboxPolicy' in updated:
                settings.pop('sandbox', None)
            session['settings'] = settings
            if updated.get('cwd'):
                session['cwd'] = updated['cwd']
            self._save(session)
            return
        if session and method == 'thread/deleted':
            self.mark_deleted(session)
            return
        if session and method in {'thread/archived', 'thread/unarchived'}:
            session['archived'] = method == 'thread/archived'
            if session['archived']:
                self._attached.discard(session['id'])
                self._active_turns = self._count_active_turns()
                if session['status'] != 'closed':
                    session['status'] = 'recovery_required'
            self._save(session)
            return
        if not session or session['id'] not in self._attached:
            return
        turn = params.get('turn', {})
        turn_id = turn.get('id', params.get('turnId'))
        if method == 'turn/started' and session['status'] in {'starting', 'ready'}:
            if session['status'] == 'ready':
                self._active_turns += 1
            session.update(status='running', active_turn_id=turn_id)
            self._save(session)
        elif method == 'turn/completed':
            if turn_id != session.get('active_turn_id'):
                return
            self.native.cancel(session['thread_id'], turn_id)
            if session.get('active_request_id'):
                path = f"requests/{session['active_request_id']}.json"
                record = self.store.read(path)
                record.update(turn_id=turn_id, status=turn.get('status', 'failed'))
                self.store.write(path, record)
            self._active_turns = max(0, self._active_turns - 1)
            status = 'closed' if session['status'] == 'closing' else 'ready'
            if session['status'] == 'recovery_required':
                status = 'recovery_required'
            session.update(status=status, active_turn_id=None, active_request_id=None)
            session.pop('turn_settings', None)
            self._save(session)
            self._cancels.pop(session['id'], None)
        elif method not in {'item/started', 'item/completed', 'item/agentMessage/delta', 'thread/tokenUsage/updated'}:
            return
        # Full native messages are captured once at the RPC boundary.

    async def runtime_failed(self, exc):
        self.ready = False
        self.native.auth_handoffs.clear()
        self.native.cancel()
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
            self.native.cancel(session['thread_id'])
            self._cancels.setdefault(session_id, threading.Event()).set()
            if session['status'] != 'closing':
                session['status'] = 'interrupting'
            self._save(session)
            await self.client.interrupt(session['thread_id'], session['active_turn_id'])
        self.events.append(session_id, {'kind': 'turn', 'public': {'status': 'pending', 'action': 'interrupt'}})
        return {'status': 'pending' if session.get('active_turn_id') else session['status']}

    async def resume(self, session_id, *, options=None):
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
            await self.client.resume(session['thread_id'], **({'options': options} if options else {}))
            if self.configuration_changed:
                await self.configuration_changed()
                session.update(self.bindings)
            self.restore_thread(session_id, thread)
            session.update(status='ready', active_turn_id=None, active_request_id=None)
            self._save(session)
            self._attached.add(session_id)
            self._cancels.pop(session_id, None)
            self._active_turns = self._count_active_turns()
            self.events.append(session_id, {'kind': 'session', 'public': {'status': 'resumed'}})
            return dict(session)

    def restore_thread(self, session_id, thread):
        self.events.append(session_id, {'kind': 'thread_restored', 'thread_id': thread['id'],
                                       'public': {'thread': thread}})

    def get_session(self, session_id):
        session = self._session(session_id)
        return dict(session) if session and not session.get('deleted') else None

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
                session = self._session(path.stem)
                if not session.get('deleted'):
                    result.append(dict(session))
            except KeyError:
                continue
        return result
