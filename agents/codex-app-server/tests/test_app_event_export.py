from pathlib import Path
import tempfile
import unittest
from codex_agent.state import AtomicJsonStore
from codex_agent.app_event_export import export_events


class ExportTests(unittest.TestCase):
    def test_stream_identity_replay_and_private_reasoning(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AtomicJsonStore(Path(directory))
            binding = {"thread_id": "thread", "host_session_id": "session"}
            events = [
                {
                    "epoch": "e",
                    "seq": str(i),
                    "method": "item/agentMessage/delta",
                    "payload": {"params": {"itemId": "message", "delta": text}},
                }
                for i, text in [(1, "hello"), (2, " world")]
            ]
            result = export_events(store, binding, "host", events)
            self.assertEqual(result[-1]["safe_content"], "hello world")
            replay = export_events(store, binding, "host", [events[-1]])
            self.assertEqual(result[-1], replay[-1])
            # Re-fetching an older page after later deltas must keep its inbox digest.
            self.assertEqual(result, export_events(store, binding, "host", events))
            private = {
                "epoch": "e",
                "seq": "3",
                "method": "item/reasoning/textDelta",
                "payload": {"params": {"delta": "private thought"}},
            }
            self.assertNotIn(
                "private thought", str(export_events(store, binding, "host", [private]))
            )
