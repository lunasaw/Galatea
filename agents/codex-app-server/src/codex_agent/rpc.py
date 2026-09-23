from __future__ import annotations

import asyncio
import json


class ProtocolError(RuntimeError):
    pass


class JsonRpcPeer:
    """One continuous reader, independently dispatched server requests, bounded RPCs."""

    def __init__(self, reader, writer, server_request=None, *, notification=None,
                 on_failure=None, timeout=30.0):
        self.reader, self.writer, self.server_request = reader, writer, server_request
        self.notification, self.on_failure, self.timeout = notification, on_failure, timeout
        self._next_id = 0
        self._seen, self._server_ids = set(), set()
        self._pending, self._handlers = {}, set()
        self._write_lock = asyncio.Lock()
        self._reader_task = None
        self._failure = None
        self._closed = asyncio.Event()

    @property
    def healthy(self):
        return self._failure is None and not self._closed.is_set()

    def start(self):
        if not self.healthy:
            raise ProtocolError("JSON-RPC peer unavailable") from self._failure
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._read_loop())

    async def send(self, message: dict):
        if not isinstance(message, dict):
            raise ProtocolError("message must be an object")
        payload = dict(message, jsonrpc="2.0")
        async with self._write_lock:
            self.writer.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode() + b"\n")
            await self.writer.drain()

    async def receive(self):
        line = await self.reader.readline()
        if not line:
            raise EOFError("JSON-RPC peer closed")
        try:
            message = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise ProtocolError("invalid JSON-RPC JSON") from exc
        # Native app-server deliberately omits the optional jsonrpc property.
        if not isinstance(message, dict) or message.get("jsonrpc", "2.0") != "2.0":
            raise ProtocolError("invalid JSON-RPC envelope")
        if "id" in message and (type(message["id"]) not in (int, str)):
            raise ProtocolError("invalid RPC id")
        if "method" in message:
            if not isinstance(message["method"], str) or not isinstance(message.get("params", {}), dict):
                raise ProtocolError("invalid RPC method/params")
        elif "id" not in message or (("result" in message) == ("error" in message)):
            raise ProtocolError("invalid RPC response")
        if "id" in message and "method" not in message:
            if message["id"] in self._seen:
                raise ProtocolError("duplicate response id")
            self._seen.add(message["id"])
        return message

    async def _dispatch(self, message):
        try:
            if self.server_request is None:
                response = {"error": {"code": -32601, "message": "Unsupported server request"}}
            else:
                response = await self.server_request(message)
            await self.send({**response, "id": message["id"]})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._fail(exc)

    async def _fail(self, exc):
        if self._failure is not None:
            return
        self._failure = exc
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._closed.set()
        if self.on_failure:
            await self.on_failure(exc)

    async def _read_loop(self):
        try:
            while self.healthy:
                message = await self.receive()
                if "method" not in message:
                    future = self._pending.get(message["id"])
                    if future is None or future.done():
                        raise ProtocolError("unexpected response id")
                    if "error" in message:
                        future.set_exception(ProtocolError("app-server rejected RPC"))
                    else:
                        future.set_result(message["result"])
                elif "id" in message:
                    if message["id"] in self._server_ids:
                        raise ProtocolError("duplicate server request id")
                    self._server_ids.add(message["id"])
                    task = asyncio.create_task(self._dispatch(message))
                    self._handlers.add(task)
                    task.add_done_callback(self._handlers.discard)
                elif self.notification:
                    await self.notification(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._fail(exc)

    async def request(self, method: str, params: dict | None = None):
        self.start()
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self.send({"id": request_id, "method": method, "params": params or {}})
            return await asyncio.wait_for(future, self.timeout)
        except (TimeoutError, asyncio.CancelledError) as exc:
            # A timed-out mutation may already have happened: never reuse this peer.
            await self._fail(exc)
            raise
        finally:
            self._pending.pop(request_id, None)

    async def wait_closed(self):
        await self._closed.wait()

    async def close(self):
        await self._fail(EOFError("JSON-RPC peer stopped"))
        tasks = [t for t in [self._reader_task, *self._handlers] if t and t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class AppServerClient:
    def __init__(self, peer: JsonRpcPeer):
        self.peer, self.initialized = peer, False

    async def initialize(self, *, experimental_api=True):
        result = await self.peer.request("initialize", {"clientInfo": {"name": "galatea-codex-agent", "version": "0.1.0"},
                                                         "capabilities": {"experimentalApi": experimental_api}})
        await self.peer.send({"method": "initialized", "params": {}})
        self.initialized = True
        return result

    async def start_thread(self, *, cwd: str, dynamic_tools: list[dict], approval_policy="never", sandbox="read-only"):
        if not self.initialized:
            raise ProtocolError("initialize is required")
        return await self.peer.request("thread/start", {"cwd": cwd, "approvalPolicy": approval_policy,
            "sandbox": sandbox, "environments": [], "dynamicTools": dynamic_tools})

    async def start_turn(self, thread_id: str, text: str):
        return await self.peer.request("turn/start", {"threadId": thread_id,
            "input": [{"type": "text", "text": text, "text_elements": []}]})

    async def resume(self, thread_id: str):
        return await self.peer.request("thread/resume", {"threadId": thread_id})

    async def interrupt(self, thread_id: str, turn_id: str):
        return await self.peer.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    async def read_thread(self, thread_id: str):
        return await self.peer.request("thread/read", {"threadId": thread_id, "includeTurns": True})

    async def stop(self):
        await self.peer.close()

    async def read_config(self, cwd: str):
        return await self.peer.request("config/read", {"cwd": cwd, "includeLayers": True})
