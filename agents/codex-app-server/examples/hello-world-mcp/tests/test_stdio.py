import sys
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

ROOT = Path(__file__).resolve().parents[1]


class HelloWorldProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_tool_and_ui_resource(self):
        async with stdio_client(StdioServerParameters(
            command=sys.executable, args=[str(ROOT / "server.py")],
        )) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = (await client.list_tools()).tools
                self.assertEqual([tool.name for tool in tools], ["draw_hello_world"])
                self.assertTrue(tools[0].annotations.readOnlyHint)
                uri = tools[0].meta["ui"]["resourceUri"]
                result = await client.call_tool("draw_hello_world", {})
                self.assertFalse(result.isError)
                self.assertEqual(result.structuredContent["message"], "Hello, World!")
                result = await client.call_tool("draw_hello_world", {"name": " 世界 "})
                self.assertEqual(result.structuredContent["message"], "Hello, 世界!")
                for arguments in ({"name": 123}, {"name": "x" * 41}, {"unexpected": True}):
                    self.assertTrue((await client.call_tool("draw_hello_world", arguments)).isError)
                self.assertTrue((await client.call_tool("unknown", {})).isError)
                resources = await client.list_resources()
                self.assertEqual(str(resources.resources[0].uri), uri)
                resource = (await client.read_resource(uri)).contents[0]
                self.assertEqual(resource.mimeType, "text/html;profile=mcp-app")
                self.assertIn("Hello, World!", resource.text)
                self.assertNotIn("<!-- APP_SCRIPT -->", resource.text)
                with self.assertRaises(McpError):
                    await client.read_resource("ui://hello-world/missing.html")
