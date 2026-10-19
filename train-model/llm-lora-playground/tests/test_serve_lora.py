from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("serve_lora", ROOT / "scripts/serve_lora.py")
assert SPEC and SPEC.loader
SERVE_LORA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVE_LORA)


class IsolatedProbeTests(unittest.TestCase):
    def test_probe_runs_in_a_one_shot_child_process(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout='{"status":"ok","adapter_effectiveness_probes":[{"probe_id":"p1"}]}\n',
            stderr="private child diagnostics must stay contained",
        )
        with patch.object(SERVE_LORA.subprocess, "run", return_value=completed) as run:
            result = SERVE_LORA._run_isolated_adapter_probe(Path("/tmp/inference.yaml"))
        self.assertEqual(result, [{"probe_id": "p1"}])
        command = run.call_args.args[0]
        self.assertIn("--probe-adapter", command)
        self.assertTrue(run.call_args.kwargs["capture_output"])

    def test_failed_child_probe_blocks_serving_without_echoing_output(self):
        completed = SimpleNamespace(returncode=2, stdout="private stdout", stderr="private stderr")
        with patch.object(SERVE_LORA.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "isolated adapter effectiveness probe failed") as raised:
                SERVE_LORA._run_isolated_adapter_probe(Path("/tmp/inference.yaml"))
        self.assertNotIn("private", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
