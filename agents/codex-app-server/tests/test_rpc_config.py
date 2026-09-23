import asyncio
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from codex_agent.config import AgentConfig
from codex_agent.rpc import JsonRpcPeer, ProtocolError


class MemoryWriter:
    def __init__(self): self.data = []
    def write(self, value): self.data.append(value)
    async def drain(self): pass


class MemoryReader:
    def __init__(self, *messages): self.messages = list(messages)
    async def readline(self): return self.messages.pop(0) if self.messages else b""


class RPCConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_request_is_dispatched_and_response_is_sent(self):
        writer = MemoryWriter()
        async def handler(message):
            self.assertEqual(message["method"], "item/tool/call")
            return {"result": {"success": True}}
        reader = MemoryReader(b'{"jsonrpc":"2.0","id":2,"method":"item/tool/call","params":{}}\n',
                             b'{"jsonrpc":"2.0","id":1,"result":{}}\n')
        peer = JsonRpcPeer(reader, writer, handler)
        await peer.request("initialize", {})
        self.assertIn(b'"id":2', writer.data[1])

    async def test_invalid_and_duplicate_messages_fail_closed(self):
        peer = JsonRpcPeer(MemoryReader(b"bad\n"), MemoryWriter())
        with self.assertRaises(ProtocolError):
            await peer.receive()
        peer = JsonRpcPeer(MemoryReader(b'{"jsonrpc":"2.0","id":1,"result":{}}\n', b'{"jsonrpc":"2.0","id":1,"result":{}}\n'), MemoryWriter())
        await peer.receive()
        with self.assertRaises(ProtocolError):
            await peer.receive()

    def test_config_uses_explicit_paths_and_environment_allowlist(self):
        config = AgentConfig.from_dict({"schema_version": "codex-agent/v1", "paths": {
            "codex_home": "home", "runtime_dir": "runtime", "state_dir": "state", "event_dir": "events",
            "workspace_dir": "workspace", "contracts": "contracts/tools.json", "catalog_metadata": "contracts/catalog-metadata.json"},
            "codex": {"binary": "runtime/bin/codex", "runtime_version": "codex-cli 0.153.4"},
            "galatea": {"principal": {"principal_id": "p", "actions": ["galatea_get_capabilities"]}}}, ROOT)
        self.assertEqual(config.codex_home, (ROOT / "home").resolve())
        env = config.child_environment()
        self.assertEqual(set(env) - {"PATH", "LANG", "LC_ALL", "OPENAI_API_KEY", "HOME", "CODEX_HOME"}, set())


if __name__ == "__main__": unittest.main()
