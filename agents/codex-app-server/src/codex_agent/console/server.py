from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from dataclasses import replace
import uuid

from .models import EventQuery, SnapshotQuery
from .ports import ObservationError, InvalidCursor, UnknownEvent, UnknownSession
from .service import CAPABILITIES
from .static_files import CSP, StaticFiles
from .html_preview import PREVIEW_CSP, read_html_preview

from ..host import HostNotReady, ResumeRejected
from ..state import identifier, payload_digest
from ..native import PROTOCOL
from ..rpc import RpcError
from ..mcp_app_context import app_identity, message_input, update_context
from .projection import redact


class BodyTooLarge(ValueError):
    pass


class ConsoleApp:
    """Same-origin Console for a trusted network; execution uses the Host principal."""

    def __init__(self, host, events, *, max_streams=32, static_dir=None, image_settings=None, integrations=None):
        self.host, self.events = host, events
        self.max_streams, self._streams = max_streams, 0
        self._writes = asyncio.Lock()
        self._stopping = False
        self._native_locks = {}
        self.image_settings = image_settings
        self.integrations = integrations
        self.static = StaticFiles(static_dir or Path(__file__).parent / 'static')

    @staticmethod
    async def _body(receive):
        chunks, size = [], 0
        while True:
            message = await asyncio.wait_for(receive(), 15)
            if message.get('type') == 'http.disconnect':
                raise ValueError('request disconnected')
            chunk = message.get('body', b'')
            size += len(chunk)
            if size > 16 * 1024 * 1024:
                raise BodyTooLarge('request body too large')
            chunks.append(chunk)
            if not message.get('more_body'):
                break
        body = json.loads(b''.join(chunks) or b'{}')
        if not isinstance(body, dict):
            raise ValueError('object body required')
        return body

    @staticmethod
    async def _send(send, status, payload, *, content_type='application/json'):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        await send({'type': 'http.response.start', 'status': status,
                    'headers': [(b'content-type', content_type.encode()), (b'cache-control', b'no-store'),
                                (b'x-content-type-options', b'nosniff'),
                                (b'content-security-policy', CSP)]})
        await send({'type': 'http.response.body', 'body': body})

    async def _lifespan(self, receive, send):
        while True:
            message = await receive()
            if message['type'] == 'lifespan.startup':
                try:
                    await self.events.start()
                    await self.host.start()
                    if self.integrations:
                        await self.integrations.start()
                except Exception:
                    await send({'type': 'lifespan.startup.failed', 'message': 'Host initialization failed'})
                    return
                await send({'type': 'lifespan.startup.complete'})
            elif message['type'] == 'lifespan.shutdown':
                self._stopping = True
                if self.integrations:
                    await self.integrations.close()
                await self.host.stop()
                await self.events.close()
                await send({'type': 'lifespan.shutdown.complete'})
                return

    @staticmethod
    def _query(scope, headers, session_id=None):
        raw = parse_qs(scope.get('query_string', b'').decode(), keep_blank_values=True)
        if any(len(values) != 1 for values in raw.values()):
            raise InvalidCursor('duplicate query parameter')
        query = {key: values[0] for key, values in raw.items()}
        if headers.get('last-event-id') and 'after' in query and headers['last-event-id'] != query['after']:
            raise InvalidCursor('Last-Event-ID and after differ')
        after = headers.get('last-event-id') or query.get('after')
        try:
            limit = int(query.get('limit', '200'))
        except ValueError as exc:
            raise InvalidCursor('invalid page size') from exc
        filters = {key: value for key, value in query.items() if key not in {'after', 'until', 'limit', 'page', 'view'}}
        if 'unbound' in filters:
            if filters['unbound'] not in {'true', 'false'}:
                raise InvalidCursor('invalid unbound filter')
            filters['unbound'] = filters['unbound'] == 'true'
        return EventQuery(session_id, after, query.get('until'), limit, filters), query

    async def _stream(self, query, receive, send):
        if self._streams >= self.max_streams:
            return await self._send(send, 429, {'error': 'STREAM_LIMIT', 'epoch': self.events.epoch})
        batch = await self.events.read_events(query)
        baseline = await self.events.read_events(replace(query, until=query.after or self.events.epoch + ':0'))
        binding_version = baseline['binding_version']
        self._streams += 1
        async def disconnect():
            while (await receive()).get('type') != 'http.disconnect':
                pass
        disconnected = asyncio.create_task(disconnect())
        async def frame(name, data, cursor=None):
            body = (('id: ' + cursor + '\n') if cursor else '') + 'event: ' + name + '\ndata: ' + json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n\n'
            await asyncio.wait_for(send({'type': 'http.response.body', 'body': body.encode(), 'more_body': True}), 10)
        try:
            await asyncio.wait_for(send({'type': 'http.response.start', 'status': 200, 'headers': [
                (b'content-type', b'text/event-stream'), (b'cache-control', b'no-store'), (b'x-accel-buffering', b'no')]}), 10)
            while True:
                if query.session_id and batch['binding_version'] != binding_version:
                    await frame('snapshot_required', {'reason': 'binding_changed', 'binding_version': batch['binding_version']})
                    break
                for event in batch['events']:
                    await frame('observation', event, event['event_id'])
                cursor = batch['next_cursor']
                await frame('cursor', {'cursor': cursor}, cursor)
                if disconnected.done():
                    break
                if batch['has_more']:
                    query = replace(query, after=cursor, until=batch['watermark'])
                else:
                    query = replace(query, after=cursor, until=None)
                    change = asyncio.create_task(self.events.wait_for_change(cursor, 1))
                    try:
                        done, _ = await asyncio.wait({change, disconnected}, return_when=asyncio.FIRST_COMPLETED)
                        if disconnected in done:
                            break
                        await change
                    finally:
                        change.cancel()
                        await asyncio.gather(change, return_exceptions=True)
                    status = await self.events.status()
                    await frame('status', status['observation'])
                batch = await self.events.read_events(query)
        except (TimeoutError, ConnectionError, ValueError, RuntimeError):
            # Headers are already on the wire: only a control frame and close.
            try:
                await frame('status', {'status': 'unavailable', 'error': 'OBSERVATION_UNAVAILABLE'})
            except (TimeoutError, ConnectionError, RuntimeError):
                pass
        finally:
            disconnected.cancel()
            await asyncio.gather(disconnected, return_exceptions=True)
            self._streams -= 1
        try:
            await asyncio.wait_for(send({'type': 'http.response.body', 'body': b'', 'more_body': False}), 10)
        except (TimeoutError, ConnectionError, RuntimeError):
            pass

    @staticmethod
    def _same_origin(scope, headers):
        if headers.get('sec-fetch-site') not in (None, 'same-origin', 'none'):
            return False
        origin = headers.get('origin')
        if origin is None:
            return True  # non-browser clients have no Origin; identity is shared.
        expected = scope.get('scheme', 'http') + '://' + headers.get('host', '')
        def normalized(value):
            parsed = urlsplit(value)
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                return None
            return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)
        try:
            return normalized(origin) is not None and normalized(origin) == normalized(expected)
        except ValueError:
            return False

    async def _write(self, path, body, headers, action):
        key = identifier(headers.get('idempotency-key') or body.get('request_id', ''))
        digest = payload_digest({'path': path, 'body': body, 'principal': self.host.principal_id})
        async with self._writes:
            record_path = f'http-requests/{key}.json'
            previous = self.host.store.read(record_path)
            if previous:
                if previous['digest'] != digest:
                    raise ValueError('idempotency conflict')
                if 'result' in previous:
                    return previous['result']
                if 'resume_rejected' in previous:
                    raise ResumeRejected('resume preflight rejected', code=previous['resume_rejected'])
                # Message admission has its own durable intent and safe deduplication.
                if not (path.startswith('/api/sessions/') and path.endswith('/messages')):
                    raise HostNotReady('request outcome unknown', code='request-outcome-unknown')
            self.host.store.write(record_path, {'digest': digest, 'status': 'intent_recorded'})
            try:
                result = await action(key)
            except ResumeRejected as exc:
                # A known preflight rejection is terminal for this request key.
                # Native resume failures remain indeterminate and must not be replayed.
                self.host.store.write(record_path, {'digest': digest, 'status': 'rejected', 'resume_rejected': exc.code})
                raise
            self.host.store.write(record_path, {'digest': digest, 'status': 'receipt_saved', 'result': result})
            return result

    async def _native_write(self, body, headers):
        key = identifier(headers.get('idempotency-key', ''))
        digest = payload_digest(body)
        lock = self._native_locks.setdefault(key, asyncio.Lock())
        async with lock:
            path = f'native-requests/{key}.json'
            previous = self.host.store.read(path)
            if previous:
                if previous['digest'] != digest:
                    raise ValueError('idempotency conflict')
                if 'result' in previous:
                    return previous['result']
                if 'error' in previous:
                    raise RpcError(previous['error'])
                if 'rejected' in previous:
                    raise ValueError(previous['rejected'])
                raise HostNotReady('native request outcome unknown; inspect runtime before retrying')
            # Schema errors must not leave an indeterminate mutation record.
            PROTOCOL.validate(PROTOCOL.schema(body['method']), body.get('params', {}))
            self.host.store.write(path, {'digest': digest, 'status': 'intent_recorded'})
            try:
                result = await self.host.native.call(body['method'], body.get('params', {}), key)
            except RpcError as exc:
                self.host.store.write(path, {'digest': digest, 'error': redact(exc.error)[0]})
                raise
            except ValueError as exc:
                self.host.store.write(path, {'digest': digest, 'rejected': str(exc)})
                raise
            self.host.store.write(path, {'digest': digest, 'result': result})
            return result

    async def __call__(self, scope, receive, send):
        if scope.get('type') == 'lifespan':
            return await self._lifespan(receive, send)
        if scope.get('type') != 'http':
            return
        method, path = scope.get('method', 'GET'), scope.get('path', '/')
        if method == 'GET' and path == '/health/live':
            return await self._send(send, 200, {'live': True})
        if method in {'GET', 'HEAD'} and await self.static.serve(scope, send):
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get('headers', [])}
        if method == 'POST' and not self._same_origin(scope, headers):
            return await self._send(send, 403, {'error': 'origin-forbidden'})
        if method == 'POST' and self._stopping:
            return await self._send(send, 503, {'error': 'not-ready-or-recovery-required'})
        try:
            if path == '/api/workspace-preview' and method == 'GET':
                query = parse_qs(scope.get('query_string', b'').decode(), keep_blank_values=True)
                if set(query) - {'path', 'revision'} or len(query.get('path', [])) != 1:
                    raise ValueError('one HTML preview path is required')
                body = await read_html_preview(self.host.native, query['path'][0], 'preview-' + uuid.uuid4().hex)
                await send({'type': 'http.response.start', 'status': 200, 'headers': [
                    (b'content-type', b'text/html; charset=utf-8'), (b'cache-control', b'no-store'),
                    (b'x-content-type-options', b'nosniff'), (b'referrer-policy', b'no-referrer'),
                    (b'content-security-policy', PREVIEW_CSP.encode()),
                    (b'permissions-policy', b'camera=(), microphone=(), geolocation=(), clipboard-write=()'),
                ]})
                return await send({'type': 'http.response.body', 'body': body})
            if path.startswith('/internal/v1/'):
                if not self.integrations:
                    return await self._send(send, 503, {'error': 'integrations-disabled'})
                try:
                    body = await self._body(receive) if method == 'POST' else {}
                    result = await self.integrations.route(method, path, body, headers, parse_qs(scope.get('query_string', b'').decode()))
                    return await self._send(send, 202 if method == 'POST' else 200, result)
                except PermissionError:
                    return await self._send(send, 401, {'error': 'service-authentication-required'})
            if path == '/api/mcp-app/activations' and method == 'GET':
                if not self.integrations:
                    return await self._send(send, 200, {'items': []})
                query = parse_qs(scope.get('query_string', b'').decode())
                session_id = query.get('session_id', [''])[0]
                self.host.get_session(session_id)
                rows = []
                for file in (self.host.store.root / 'app-activations').glob('*.json'):
                    row = self.host.store.read('app-activations/' + file.name)
                    if row.get('source_session_id') == session_id or row.get('session_id') == session_id:
                        rows.append({k: row.get(k) for k in ('activation_id', 'state', 'source_session_id', 'session_id', 'thread_id', 'app')})
                return await self._send(send, 200, {'items': rows})
            if path in {'/api/mcp-app/challenges', '/api/mcp-app/confirmations', '/api/mcp-app/tool-calls'} and method == 'POST':
                if not self.integrations:
                    return await self._send(send, 503, {'error': 'trusted-app-gateway-disabled'})
                # _same_origin above validates a mandatory Origin here, including port.
                # HTTP IP origins omit Sec-Fetch-*; opaque frames still send Origin:null
                # and cannot confirm. Present fetch metadata must remain same-origin.
                if path.endswith('/confirmations') and (
                    not headers.get('origin')
                    or headers.get('sec-fetch-site') not in (None, 'same-origin')
                ):
                    return await self._send(send, 403, {'error': 'host-ui-confirmation-required'})
                body = await self._body(receive)
                async with self._writes:
                    gateway = self.integrations.gateway
                    result = gateway.challenge(body) if path.endswith('/challenges') else gateway.confirm(body) if path.endswith('/confirmations') else await gateway.call(body)
                return await self._send(send, 200, result)
            if path in {'/api/image-generation/settings', '/api/image-generation/check'}:
                if self.image_settings is None:
                    return await self._send(send, 503, {'error': '图像设置服务尚未加载，请更新并重启此实例。'})
                if method not in {'GET', 'POST'} or method == 'GET' and path.endswith('/check'):
                    return await self._send(send, 405, {'error': 'method-not-allowed'})
                self.host.assert_ready()
                observed = await self.host.client.read_config(str(self.host.workspace_dir))
                if method == 'GET':
                    return await self._send(send, 200, self.image_settings.public(observed['config']))
                body = await self._body(receive)
                if path.endswith('/check'):
                    result = await asyncio.to_thread(self.image_settings.check, body, observed['config'])
                else:
                    async def save_images(_key):
                        result = await asyncio.to_thread(self.image_settings.save, body, observed['config'])
                        try:
                            await self.host.client.peer.request('skills/list', {'cwds': [str(self.host.workspace_dir)], 'forceReload': True})
                        except Exception:
                            result['warning'] = '配置已保存；技能列表刷新失败，请在 Skills 中刷新或开始新聊天。'
                        return result
                    result = await self._write(path, body, headers, save_images)
                return await self._send(send, 200, result)
            if path in {'/api/mcp-app/context', '/api/mcp-app/messages'} and method == 'POST':
                body = await self._body(receive)
                app_identity(body)
                session = self.host._session_for_thread(body.get('threadId'))
                if not session or session.get('principal_id') != self.host.principal_id:
                    raise UnknownSession('MCP App thread does not belong to this Host')
                async def app_action(key):
                    if path.endswith('/context'):
                        return update_context(self.host.store, session['id'], body)
                    inputs = message_input(body.get('message'))
                    current = self.host._session_for_thread(body['threadId'])
                    if not current or current.get('status') not in {'ready', 'running'}:
                        raise HostNotReady('MCP App thread is not ready')
                    # The app cannot choose another thread or a turn id. Resolve
                    # both from its bound Host session; native steer checks races.
                    turn = current.get('active_turn_id')
                    params = {'threadId': current['thread_id'], 'input': inputs}
                    method = 'turn/steer' if turn else 'turn/start'
                    if turn:
                        params['expectedTurnId'] = turn
                    result = await self.host.native.call(method, params, key)
                    if result.get('status') == 'unknown':
                        raise HostNotReady('MCP App message outcome unknown')
                    return {}
                return await self._send(send, 200, await self._write(path, body, headers, app_action))
            if path == '/api/mcp-app' and method == 'GET':
                from .mcp_apps import resource_document
                query = parse_qs(scope.get('query_string', b'').decode(), keep_blank_values=True)
                if set(query) != {'threadId', 'server', 'uri'} or any(len(v) != 1 for v in query.values()):
                    raise ValueError('invalid MCP application resource')
                params = {key: values[0] for key, values in query.items()}
                if not params['uri'].startswith('ui://'):
                    raise ValueError('MCP application requires a ui resource')
                result = await self.host.native.call('mcpServer/resource/read', params, 'app-' + uuid.uuid4().hex)
                body, policy = resource_document(result, params['uri'], headers.get('host', ''))
                await send({'type': 'http.response.start', 'status': 200, 'headers': [
                    (b'content-type', b'text/html; charset=utf-8'), (b'cache-control', b'no-store'),
                    (b'x-content-type-options', b'nosniff'), (b'referrer-policy', b'no-referrer'),
                    (b'content-security-policy', policy.encode()),
                    (b'permissions-policy', b'camera=(), microphone=(), geolocation=(), clipboard-write=()'),
                ]})
                return await send({'type': 'http.response.body', 'body': body})
            if path == '/health/ready' and method == 'GET':
                self.host.assert_ready()
                return await self._send(send, 200, {'ready': True})
            if path == '/api/capabilities' and method == 'GET':
                return await self._send(send, 200, {**CAPABILITIES, 'storage': (await self.events.status())['storage'], 'catalog_digest': self.host.registry.catalog.digest,
                    'principal_id': self.host.principal_id, 'tools': self.host.registry.dynamic_tools(self.host.actions)})
            if path == '/api/native/protocol' and method == 'GET':
                return await self._send(send, 200, {**PROTOCOL.catalog(), 'cwd': str(self.host.workspace_dir),
                                                   'skill_roots': getattr(self.host, 'skill_roots', []),
                                                   'platform': sys.platform,
                                                   'configuration_editable': bool(self.host.configuration_changed)})
            if path == '/api/native/config-schema' and method == 'GET':
                from ..runtime_version import PROTOCOL_PATH
                schema = json.loads(PROTOCOL_PATH.with_name('config.schema.json').read_text())
                return await self._send(send, 200, schema)
            if path == '/api/native/auth-handoffs' and method == 'GET':
                return await self._send(send, 200, self.host.native.list_auth_handoffs())
            if path.startswith('/api/native/receipts/') and method == 'GET':
                key = identifier(path.rsplit('/', 1)[1])
                receipt = self.host.store.read(f'native-requests/{key}.json')
                return await self._send(send, 200 if receipt else 404, receipt or {'error': 'receipt-not-found'})
            if path == '/api/sidebar-groups' and method == 'GET':
                return await self._send(send, 200, self.host.store.read('console/sidebar-groups.json') or {'sections': [], 'revision': 0})
            if path == '/api/sidebar-groups' and method == 'POST':
                body = await self._body(receive)
                async def save_groups(_key):
                    sections = body.get('sections')
                    if (not isinstance(sections, list) or len(sections) > 1000
                            or any(not isinstance(s, dict) or set(s) != {'id', 'name'}
                                   or not isinstance(s['id'], str) or not s['id']
                                   or not isinstance(s['name'], str) or not s['name'].strip()
                                   or len(s['name']) > 256 for s in sections)
                            or len({s['id'] for s in sections}) != len(sections)):
                        raise ValueError('invalid project groups')
                    previous = self.host.store.read('console/sidebar-groups.json') or {'revision': 0}
                    if body.get('revision') != previous['revision']:
                        raise ValueError('project groups changed in another client; refresh and retry')
                    result = {'sections': sections, 'revision': previous['revision'] + 1}
                    self.host.store.write('console/sidebar-groups.json', result)
                    return result
                return await self._send(send, 200, await self._write(path, body, headers, save_groups))
            if path == '/api/native/requests' and method == 'GET':
                return await self._send(send, 200, self.host.native.list_pending())
            if path.startswith('/api/native/requests/') and method == 'POST':
                body = await self._body(receive)
                key = identifier(path.rsplit('/', 1)[1])
                return await self._send(send, 200, self.host.native.respond(key, body.get('result'), body.get('error')))
            if path == '/api/native/call' and method == 'POST':
                body = await self._body(receive)
                return await self._send(send, 200, await self._native_write(body, headers))
            if path == '/api/console/status' and method == 'GET':
                status = await self.events.status()
                try:
                    self.host.assert_ready()
                    status['host_ready'] = True
                except HostNotReady:
                    status['host_ready'] = False
                return await self._send(send, 200, status)
            if path.startswith('/api/events/') and method == 'GET':
                query, _ = self._query(scope, headers)
                action = path[len('/api/events/'):]
                if action == 'history':
                    return await self._send(send, 200, await self.events.read_events(query))
                if action == 'stream':
                    return await self._stream(query, receive, send)
                return await self._send(send, 200, await self.events.detail(action))
            if path == '/api/sessions':
                if method == 'GET':
                    return await self._send(send, 200, self.host.list_sessions())
                if method == 'POST':
                    body = await self._body(receive)
                    async def create(key):
                        kwargs = {'options': body['options']} if body.get('options') else {}
                        return await self.host.create_session(body.get('session_id') or 'session-' + payload_digest([self.host.principal_id, key]), **kwargs)
                    return await self._send(send, 200, await self._write(path, body, headers, create))
            parts = path.strip('/').split('/')
            if len(parts) in (3, 4, 5) and parts[:2] == ['api', 'sessions']:
                session_id = identifier(parts[2])
                session = self.host.get_session(session_id)
                if session is None or session.get('principal_id') != self.host.principal_id:
                    raise UnknownSession('Host session does not exist')
                if method == 'GET' and len(parts) == 3:
                    return await self._send(send, 200, session)
                action = parts[3] if len(parts) >= 4 else None
                if method == 'GET':
                    query, raw = self._query(scope, headers, session_id)
                    if action == 'events':
                        return await self._stream(query, receive, send)
                    if action == 'history':
                        return await self._send(send, 200, await self.events.read_events(query))
                    if action in {'snapshot', 'messages', 'loop'}:
                        turn_id = identifier(parts[4]) if len(parts) == 5 and action == 'loop' else None
                        view = 'messages' if action == 'messages' else 'steps' if turn_id else raw.get('view', 'all')
                        snapshot = await self.events.snapshot(SnapshotQuery(session_id, turn_id, view, query.until, query.limit, raw.get('page')))
                        return await self._send(send, 200, snapshot)
                if method == 'POST' and action in {'messages', 'interrupt', 'close', 'resume'}:
                    body = await self._body(receive)
                    async def invoke(key):
                        if action == 'messages':
                            if body.get('request_id', key) != key:
                                raise ValueError('idempotency key mismatch')
                            kwargs = {k: body[k] for k in ('input_items', 'options') if k in body}
                            return await self.host.send_message(session_id, key, body.get('text', ''), **kwargs)
                        fn = {'interrupt': self.host.interrupt, 'close': self.host.close_session, 'resume': self.host.resume}[action]
                        return await fn(session_id)
                    return await self._send(send, 200, await self._write(path, body, headers, invoke))
            return await self._send(send, 404, {'error': 'not-found'})
        except ObservationError as exc:
            return await self._send(send, exc.status, {'error': exc.code, 'epoch': self.events.epoch})
        except BodyTooLarge:
            return await self._send(send, 413, {'error': 'body-too-large'})
        except RpcError as exc:
            return await self._send(send, 422, {'error': str(exc), 'native_error': redact(exc.error)[0]})
        except ResumeRejected as exc:
            return await self._send(send, 409, {'error': exc.code, 'retry_safe': True})
        except HostNotReady as exc:
            return await self._send(send, 503, {'error': exc.code})
        except ValueError as exc:
            return await self._send(send, 409, {'error': str(exc) if path.startswith(('/api/native/', '/api/image-generation/')) else 'invalid-or-conflicting-request'})
        except (KeyError, TimeoutError):
            return await self._send(send, 409, {'error': 'invalid-or-conflicting-request'})
        except Exception:
            return await self._send(send, 503, {'error': 'state-or-runtime-unavailable'})
