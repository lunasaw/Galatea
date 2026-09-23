"""Official MCP transports, fixed restricted service identity and bounded results."""
from __future__ import annotations
import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Tool, CallToolResult, TextContent, ToolAnnotations
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from .contracts import TOOLS
from .envelope import MUTATIONS, call_envelope



def make_server(service, principal):
    server = Server('galatea', version='0.1.0')

    @server.list_tools()
    async def list_tools():
        return [Tool(name=name, description=f"Governed {name.removeprefix('galatea_').replace('_', ' ')}; "
                     "bound to administrator-approved campaign and immutable inputs.", inputSchema=schema,
                     annotations=ToolAnnotations(readOnlyHint=name.removeprefix('galatea_') not in MUTATIONS,
                                                 destructiveHint=False, idempotentHint=True))
                for name, schema in TOOLS.items() if '*' in principal.actions or name in principal.actions]

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        payload = await asyncio.to_thread(call_envelope, service, principal, name, arguments)
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        return CallToolResult(isError=not payload['ok'], structuredContent=payload,
                              content=[TextContent(type='text', text=encoded)])
    return server


async def watchdog(service, interval=15):
    while True:
        try:
            await asyncio.to_thread(service.reconcile_all)
        except Exception:
            logging.getLogger(__name__).error('Campaign reconciliation failed; state preserved')
        await asyncio.sleep(interval)


def create_http_app(service, principal, token, *, background=True):
    if not token or len(token) < 16:
        raise ValueError('A strong service bearer token is required')
    server = make_server(service, principal)
    manager = StreamableHTTPSessionManager(server, stateless=True, json_response=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=['127.0.0.1:*','localhost:*'], allowed_origins=['http://127.0.0.1:*','http://localhost:*']))

    async def endpoint(scope, receive, send):
        headers = dict(scope.get('headers', []))
        supplied = headers.get(b'authorization', b'')
        if not hmac.compare_digest(supplied, ('Bearer ' + token).encode()):
            await JSONResponse({'error':'unauthorized'}, status_code=401)(scope, receive, send)
            return
        await manager.handle_request(scope, receive, send)

    @asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            task = asyncio.create_task(watchdog(service)) if background else None
            try:
                yield
            finally:
                if task:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    class MCPHTTP:
        async def __call__(self, scope, receive, send):
            await endpoint(scope, receive, send)

    return Starlette(routes=[Route('/mcp', endpoint=MCPHTTP(), methods=['GET', 'POST', 'DELETE'])], lifespan=lifespan)


async def serve_stdio(service, principal):
    from mcp.server.stdio import stdio_server
    server = make_server(service, principal)
    task = asyncio.create_task(watchdog(service))
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
