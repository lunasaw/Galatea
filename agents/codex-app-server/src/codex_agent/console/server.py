from __future__ import annotations

import asyncio
import hmac
import json
from pathlib import Path
from urllib.parse import parse_qs

from ..host import HostNotReady
from ..state import identifier, payload_digest


class BodyTooLarge(ValueError):
    pass


class ConsoleApp:
    """Authenticated ASGI API and live SSE with bounded reads and socket backpressure."""

    def __init__(self, host, events, *, token, allowed_origins, max_streams=32):
        if not token or len(token) < 16:
            raise ValueError('strong bearer token required')
        self.host, self.events, self.token = host, events, token
        self.allowed_origins = frozenset(allowed_origins)
        self.max_streams, self._streams = max_streams, 0
        self._writes = asyncio.Lock()

    @staticmethod
    async def _body(receive):
        chunks, size = [], 0
        while True:
            message = await asyncio.wait_for(receive(), 15)
            if message.get('type') == 'http.disconnect':
                raise ValueError('request disconnected')
            chunk = message.get('body', b'')
            size += len(chunk)
            if size > 1024 * 1024:
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
                                (b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")]})
        await send({'type': 'http.response.body', 'body': body})

    async def _lifespan(self, receive, send):
        while True:
            message = await receive()
            if message['type'] == 'lifespan.startup':
                try:
                    await self.host.start()
                except Exception:
                    await send({'type': 'lifespan.startup.failed', 'message': 'Host initialization failed'})
                    return
                await send({'type': 'lifespan.startup.complete'})
            elif message['type'] == 'lifespan.shutdown':
                await self.host.stop()
                await send({'type': 'lifespan.shutdown.complete'})
                return

    async def _stream(self, session_id, after, receive, send):
        if self._streams >= self.max_streams:
            return await self._send(send, 429, {'error': 'stream-limit'})
        batch = self.events.replay(session_id, after)
        self._streams += 1
        async def disconnect():
            while (await receive()).get('type') != 'http.disconnect':
                pass
        disconnected = asyncio.create_task(disconnect())
        try:
            await send({'type': 'http.response.start', 'status': 200, 'headers': [
                (b'content-type', b'text/event-stream'), (b'cache-control', b'no-store'), (b'x-accel-buffering', b'no')]})
            heartbeat = 0
            while True:
                if batch:
                    body = b''.join(b'id: ' + str(e['seq']).encode() + b'\ndata: ' +
                        json.dumps(e, ensure_ascii=False, separators=(',', ':')).encode() + b'\n\n' for e in batch)
                    await asyncio.wait_for(send({'type': 'http.response.body', 'body': body, 'more_body': True}), 10)
                    after = batch[-1]['seq']
                if disconnected.done():
                    break
                done, _ = await asyncio.wait({disconnected}, timeout=.1)
                if done:
                    break
                batch = self.events.replay(session_id, after)
                heartbeat += 1
                if not batch and heartbeat % 150 == 0:
                    await asyncio.wait_for(send({'type': 'http.response.body', 'body': b': keepalive\n\n', 'more_body': True}), 10)
        except (TimeoutError, ConnectionError, ValueError, RuntimeError):
            # Headers may already be on the wire; terminate without a second response.
            pass
        finally:
            disconnected.cancel()
            await asyncio.gather(disconnected, return_exceptions=True)
            self._streams -= 1

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
                # Message admission has its own durable intent and safe deduplication.
                if not path.endswith('/messages'):
                    raise HostNotReady('request outcome unknown')
            self.host.store.write(record_path, {'digest': digest, 'status': 'intent_recorded'})
            result = await action(key)
            self.host.store.write(record_path, {'digest': digest, 'status': 'receipt_saved', 'result': result})
            return result

    async def __call__(self, scope, receive, send):
        if scope.get('type') == 'lifespan':
            return await self._lifespan(receive, send)
        if scope.get('type') != 'http':
            return
        method, path = scope.get('method', 'GET'), scope.get('path', '/')
        if method == 'GET' and path == '/health/live':
            return await self._send(send, 200, {'live': True})
        if method == 'GET' and path in {'/', '/app.js', '/style.css'}:
            filename, content_type = {'/': ('index.html', 'text/html; charset=utf-8'),
                '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}[path]
            return await self._send(send, 200, (Path(__file__).parent / 'static' / filename).read_bytes(), content_type=content_type)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get('headers', [])}
        if not hmac.compare_digest(headers.get('authorization', ''), 'Bearer ' + self.token):
            return await self._send(send, 401, {'error': 'unauthorized'})
        if headers.get('origin') and headers['origin'] not in self.allowed_origins:
            return await self._send(send, 403, {'error': 'origin-forbidden'})
        if method == 'POST' and headers.get('origin') not in self.allowed_origins:
            return await self._send(send, 403, {'error': 'origin-forbidden'})
        try:
            if path == '/health/ready' and method == 'GET':
                self.host.assert_ready()
                return await self._send(send, 200, {'ready': True})
            if path == '/api/capabilities' and method == 'GET':
                return await self._send(send, 200, {'catalog_digest': self.host.registry.catalog.digest,
                    'principal_id': self.host.principal_id, 'tools': self.host.registry.dynamic_tools(self.host.actions)})
            if path == '/api/sessions':
                if method == 'GET':
                    return await self._send(send, 200, self.host.list_sessions())
                if method == 'POST':
                    body = await self._body(receive)
                    async def create(key):
                        return await self.host.create_session(body.get('session_id') or 'session-' + payload_digest([self.host.principal_id, key]))
                    return await self._send(send, 200, await self._write(path, body, headers, create))
            parts = path.strip('/').split('/')
            if len(parts) in (3, 4, 5) and parts[:2] == ['api', 'sessions']:
                session_id = identifier(parts[2])
                session = self.host.get_session(session_id)
                if session is None or session.get('principal_id') != self.host.principal_id:
                    return await self._send(send, 404, {'error': 'unknown-session'})
                if method == 'GET' and len(parts) == 3:
                    return await self._send(send, 200, session)
                if len(parts) == 5:
                    if method == 'GET' and parts[3] == 'loop':
                        turn_id = identifier(parts[4])
                        loop = self.events.loop(session_id)
                        loop['steps'] = [step for step in loop['steps'] if step.get('turn_id') == turn_id]
                        return await self._send(send, 200, loop)
                    return await self._send(send, 404, {'error': 'not-found'})
                action = parts[3] if len(parts) == 4 else None
                if action == 'events' and method == 'GET':
                    query = parse_qs(scope.get('query_string', b'').decode())
                    after = int(headers.get('last-event-id', query.get('after', ['0'])[0]))
                    return await self._stream(session_id, after, receive, send)
                if action == 'loop' and method == 'GET':
                    return await self._send(send, 200, self.events.loop(session_id))
                if method == 'POST' and action in {'messages', 'interrupt', 'close', 'resume'}:
                    body = await self._body(receive)
                    async def invoke(key):
                        if action == 'messages':
                            if body.get('request_id', key) != key:
                                raise ValueError('idempotency key mismatch')
                            return await self.host.send_message(session_id, key, body['text'])
                        fn = {'interrupt': self.host.interrupt, 'close': self.host.close_session, 'resume': self.host.resume}[action]
                        return await fn(session_id)
                    return await self._send(send, 200, await self._write(path, body, headers, invoke))
            return await self._send(send, 404, {'error': 'not-found'})
        except BodyTooLarge:
            return await self._send(send, 413, {'error': 'body-too-large'})
        except HostNotReady:
            return await self._send(send, 503, {'error': 'not-ready-or-recovery-required'})
        except (ValueError, KeyError, TimeoutError):
            return await self._send(send, 409, {'error': 'invalid-or-conflicting-request'})
        except Exception:
            return await self._send(send, 503, {'error': 'state-or-runtime-unavailable'})
