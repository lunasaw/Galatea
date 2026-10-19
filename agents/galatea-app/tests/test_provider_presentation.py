import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from codex_agent.registry import ToolContext
from galatea_app.integrations.codex_provider import create_provider


class ProviderPresentationTests(unittest.TestCase):
    def test_native_result_binds_view_to_returned_task_and_preserves_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "provider.token"
            key.write_text("test-credential")
            key.chmod(0o600)
            responses = [httpx.Response(200, json={"task_id": "t"}),
                         httpx.Response(200, json={"task_id": None, "task": None}),
                         httpx.Response(403, json={"code": "FORBIDDEN"})]
            with patch("galatea_app.integrations.codex_provider.httpx.Client") as client:
                client.return_value.post.side_effect = responses
                provider = create_provider({"credential_file": str(key), "service_url": "http://test",
                    "host_id": "host", "workspace_ref": "work"})
                ctx = ToolContext("chat", "thread", "turn", "call", "alice", cancel=threading.Event())
                result = provider.handlers["start_task"](ctx, {"goal": "inspect", "command_id": "start"})
                self.assertEqual(result.data["app_view"], {"server": "galatea_app",
                    "resource_uri": "ui://galatea/workbench/v1", "arguments": {"task_id": "t"}})
                self.assertNotIn("app_view", provider.handlers["get_current_task"](ctx, {}).data)
                self.assertFalse(provider.handlers["start_task"](ctx, {}).ok)
