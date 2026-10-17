"""A read-only stdio MCP server with an interactive Hello World resource."""
from __future__ import annotations

import asyncio
from pathlib import Path

from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, Resource, TextContent, Tool, ToolAnnotations

ROOT = Path(__file__).resolve().parent
RESOURCE_URI = "ui://hello-world/app.html"
MIME_TYPE = "text/html;profile=mcp-app"


def make_server() -> Server:
    server = Server("hello-world", version="1.0.0")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [Tool(
            name="draw_hello_world",
            title="Hello World",
            description="Draw an interactive Hello World greeting. Optionally greet a name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "maxLength": 40, "default": "World"},
                },
                "additionalProperties": False,
            },
            annotations=ToolAnnotations(
                readOnlyHint=True, destructiveHint=False,
                idempotentHint=True, openWorldHint=False,
            ),
            _meta={"ui": {"resourceUri": RESOURCE_URI}},
        )]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> CallToolResult:
        if name != "draw_hello_world":
            raise ValueError(f"Unknown tool: {name}")
        recipient = arguments.get("name", "World").strip() or "World"
        message = f"Hello, {recipient}!"
        return CallToolResult(
            content=[TextContent(type="text", text=message)],
            structuredContent={"name": recipient, "message": message},
        )

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        return [Resource(
            uri=RESOURCE_URI, name="Hello World",
            description="An interactive SVG greeting card.",
            mimeType=MIME_TYPE,
        )]

    @server.read_resource()
    async def read_resource(uri) -> list[ReadResourceContents]:
        if str(uri) != RESOURCE_URI:
            raise ValueError(f"Unknown resource: {uri}")
        return [ReadResourceContents(
            content=(ROOT / "dist/app.html").read_text(encoding="utf-8"),
            mime_type=MIME_TYPE,
        )]

    return server


async def main() -> None:
    if not (ROOT / "dist/app.html").is_file():
        raise SystemExit("Build the App first: node build.mjs")
    server = make_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
