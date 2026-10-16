"""Versioned native protocol bridge and human response broker.

The schema is exported by the pinned runtime, including experimental fields.
Business tools continue through ToolRegistry; this module never executes tools.
"""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import time
import uuid

from jsonschema import Draft7Validator

from .console.observer import observation_context
from .console.projection import redact
from .state import payload_digest


class NativeProtocol:
    def __init__(self):
        self.bundle = json.loads((Path(__file__).parent / 'protocol/0.154.0.json').read_text())
        self.methods = {row['properties']['method']['enum'][0]: row
                        for row in self.bundle['client']['oneOf']}

    def schema(self, method):
        row = self.methods.get(method)
        if not row:
            raise ValueError('unknown native method')
        return {**row['properties'].get('params', {'type': 'object'}),
                'definitions': self.bundle['client']['definitions']}

    @staticmethod
    def validate(schema, value):
        error = next(Draft7Validator(schema).iter_errors(value), None)
        if error:
            # Do not include rejected values (which can contain credentials).
            path = '.'.join(str(p) for p in error.absolute_path) or 'params'
            raise ValueError(f'invalid {path}: {error.validator}')

    def catalog(self):
        return {'version': self.bundle['version'], 'methods': sorted(set(self.methods) - {'initialize'}),
                'definitions': self.bundle['client']['definitions'],
                'params': {key: value['properties'].get('params', {}) for key, value in self.methods.items()
                           if key != 'initialize'}}


PROTOCOL = NativeProtocol()


class NativeBridge:
    def __init__(self, host):
        self.host = host
        self.pending = {}
        self.completed = {}

    async def request(self, message):
        method, params = message['method'], message.get('params', {})
        if method == 'currentTime/read':
            return {'result': {'currentTimeAt': int(time.time())}}
        if method not in PROTOCOL.bundle['responses']:
            return {'error': {'code': -32601, 'message': 'Unknown server request'}}
        thread_id = params.get('threadId', params.get('conversationId'))
        session = self.host._session_for_thread(thread_id)
        # Requests from subagents are deliberately visible with their native
        # thread identity; they must not be assigned to an unrelated chat.
        key = 'approval-' + uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        row = {'id': key, 'rpc_id': message['id'], 'method': method, 'params': copy.deepcopy(params),
               'session_id': session['id'] if session else None, 'created_at': time.time(),
               'response_schema': PROTOCOL.bundle['responses'][method]}
        self.pending[key] = (row, future)
        self.host.events.append(row['session_id'], {'kind': 'interaction_pending', 'thread_id': thread_id,
                                                   'public': {'id': key, 'method': method}})
        try:
            return await future
        finally:
            self.pending.pop(key, None)
            self.host.events.append(row['session_id'], {'kind': 'interaction_resolved', 'thread_id': thread_id,
                                                       'public': {'id': key}})

    def list_pending(self):
        return [copy.deepcopy(row) for row, future in self.pending.values() if not future.done()]

    def respond(self, key, result=None, error=None):
        if (result is None) == (error is None):
            raise ValueError('supply either result or error')
        response = {'result': result} if error is None else {'error': error}
        digest = payload_digest(response)
        if key in self.completed:
            if self.completed[key] != digest:
                raise ValueError('response already submitted')
            return {'status': 'submitted'}
        if key not in self.pending:
            raise ValueError('request expired or runtime restarted')
        row, future = self.pending[key]
        if future.done():
            raise ValueError('request expired')
        if error is None:
            PROTOCOL.validate(row['response_schema'], result)
            decisions = row['params'].get('availableDecisions')
            if decisions is not None and result.get('decision') not in decisions:
                raise ValueError('decision is not offered by the runtime')
            if row['method'] == 'item/tool/requestUserInput':
                ids = {q['id'] for q in row['params']['questions']}
                if set(result['answers']) != ids:
                    raise ValueError('answer every question')
        elif not isinstance(error, dict) or type(error.get('code')) is not int or not isinstance(error.get('message'), str):
            raise ValueError('invalid RPC error')
        self.completed[key] = digest
        future.set_result(response)
        return {'status': 'submitted'}

    def cancel(self, thread_id=None, turn_id=None):
        for row, future in self.pending.values():
            params = row['params']
            if ((thread_id is None or params.get('threadId', params.get('conversationId')) == thread_id)
                    and (turn_id is None or params.get('turnId') == turn_id) and not future.done()):
                future.set_result({'error': {'code': -32000, 'message': 'Request cancelled'}})

    async def call(self, method, params, request_id):
        if method in {'thread/fork', 'thread/archive', 'thread/unarchive', 'thread/delete', 'thread/rollback', 'thread/revert'}:
            async with self.host._admission:
                return await self._call(method, params, request_id)
        return await self._call(method, params, request_id)

    async def _call(self, method, params, request_id):
        host = self.host
        if method == 'initialize' or method not in PROTOCOL.methods:
            raise ValueError('method is managed by Host or unknown')
        PROTOCOL.validate(PROTOCOL.schema(method), params)
        await host._check_admission()
        if method.startswith(('config/value/', 'config/batch', 'externalAgentConfig/import')) or method in {
                'experimentalFeature/enablement/set', 'skills/config/write', 'skills/extraRoots/set',
                'plugin/install', 'plugin/uninstall', 'plugin/reconcile',
                'marketplace/add', 'marketplace/remove', 'marketplace/upgrade'}:
            if not host.configuration_changed:
                raise ValueError('immutable release configuration; use a development Host to edit settings')
            changes_config = True
        else:
            changes_config = False
        thread_id = (params or {}).get('threadId')
        session = host._session_for_thread(thread_id)
        if method == 'thread/start':
            options = dict(params)
            options.pop('dynamicTools', None)
            session = await host.create_session('session-' + request_id, options=options)
            return {'thread': {'id': session['thread_id']}, 'session': session}
        if method == 'turn/start':
            if not session:
                raise ValueError('resume the thread before starting a turn')
            options = {k: v for k, v in params.items() if k not in {'threadId', 'input'}}
            text = '\n'.join(p['text'] for p in params['input'] if p['type'] == 'text')
            return await host.send_message(session['id'], request_id, text, input_items=params['input'], options=options)
        if method == 'turn/interrupt' and session:
            if params['turnId'] != session.get('active_turn_id'):
                raise ValueError('turn identity changed')
            return await host.interrupt(session['id'])
        if method == 'thread/resume' and session:
            if params.get('path') or params.get('history'):
                raise ValueError('resume a Host session by its thread identity')
            options = {k: v for k, v in params.items() if k != 'threadId'}
            return {'session': await host.resume(session['id'], options=options)}
        if session and (session.get('active_turn_id') or session['status'] in {'starting', 'running', 'interrupting', 'closing'}) and method in {
                'thread/resume', 'thread/fork', 'thread/archive', 'thread/delete', 'thread/rollback', 'thread/revert', 'review/start'}:
            raise ValueError('stop the active turn first')
        context = {'thread_id': thread_id, 'session_id': session['id'] if session else None,
                   'host_request_id': request_id}
        # A review starts an agent turn just like turn/start, so reserve its
        # admission slot before the native notification can arrive.
        if method == 'review/start':
            if not session or session['status'] != 'ready' or params.get('delivery') == 'detached':
                raise ValueError('start an inline review on a ready session')
            async with host._admission:
                if host._active_turns >= host.max_active_turns:
                    raise ValueError('active turn limit reached')
                host.store.write(f'requests/{request_id}.json', {'request_id': request_id, 'status': 'intent_recorded'})
                session.update(status='starting', active_request_id=request_id, active_turn_id=None)
                host._save(session)
                host._active_turns += 1
        try:
            with observation_context(**context):
                result = await host.client.peer.request(method, params, timeout=600 if method in {
                    'command/exec', 'mcpServer/tool/call', 'account/login/start', 'mcpServer/oauth/login'} else 60)
        except BaseException:
            if method == 'review/start':
                session['status'] = 'recovery_required'
                host._save(session)
            raise
        if changes_config:
            await host.configuration_changed()
        if method in {'thread/resume', 'thread/fork'}:
            if host.configuration_changed:
                await host.configuration_changed()
            source_settings = dict(session.get('settings', {})) if session else {}
            thread = result['thread']
            if thread.get('historyMode') == 'paginated':
                thread = (await host.client.read_thread(thread['id']))['thread']
            session = host._session_for_thread(thread['id'])
            if not session:
                session = {'id': 'session-' + request_id, 'thread_id': thread['id'], 'principal_id': host.principal_id,
                           **host.bindings, 'status': 'ready', 'active_turn_id': None, 'active_request_id': None}
            active = next((turn for turn in reversed(thread.get('turns', [])) if turn.get('status') == 'inProgress'), None)
            session.update(status='running' if active else 'ready', archived=False,
                           active_turn_id=active['id'] if active else None, active_request_id=None,
                           cwd=result.get('cwd', thread.get('cwd', str(host.workspace_dir))))
            session['settings'] = {**source_settings, **session.get('settings', {}),
                **{key: result[key] for key in ('model', 'modelProvider', 'approvalPolicy', 'approvalsReviewer',
                   'sandboxPolicy', 'permissions', 'cwd') if key in result}}
            if 'reasoningEffort' in result:
                session['settings']['effort'] = result['reasoningEffort']
            host._save(session)
            host._attached.add(session['id'])
            host._active_turns = sum(bool(host._sessions[s].get('active_turn_id')) for s in host._attached)
            host.events.bind(session['id'], thread['id'])
            host.restore_thread(session['id'], thread)
            result['session'] = dict(session)
        if session and method in {'thread/archive', 'thread/unarchive', 'thread/delete', 'thread/name/set'}:
            if method == 'thread/name/set':
                session['name'] = params['name']
            else:
                session.update(archived=method != 'thread/unarchive')
                if method == 'thread/delete':
                    host.mark_deleted(session)
            host._save(session)
        if session and method in {'thread/rollback', 'thread/revert'}:
            thread = result.get('thread')
            if method == 'thread/revert' or not thread:
                thread = (await host.client.read_thread(thread_id))['thread']
            host.restore_thread(session['id'], thread)
        if session and method in {'thread/settings/update', 'turn/settings/update'}:
            session['settings'] = {**session.get('settings', {}), **{k: v for k, v in params.items() if k != 'threadId'}}
            if params.get('cwd'):
                session['cwd'] = params['cwd']
            host._save(session)
        public = redact(result)[0]
        if method in {'account/login/start', 'mcpServer/oauth/login'}:
            # OAuth authorization URLs are user-facing protocol output, not
            # bearer credentials. Keep them usable while audit logs redact.
            for key in ('authorizationUrl', 'authUrl', 'url'):
                value = result.get(key)
                if isinstance(value, str) and value.startswith(('https://', 'http://')):
                    public[key] = value
        return public
