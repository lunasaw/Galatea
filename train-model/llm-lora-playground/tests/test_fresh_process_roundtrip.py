from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from llm_lora_playground.tracking import ArtifactIntegrityError, _load_adapter_in_fresh_process


class FreshProcessRoundtripTests(unittest.TestCase):
    @patch("llm_lora_playground.tracking.subprocess.run")
    def test_adapter_loader_uses_new_cpu_only_python_process(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "fresh-process-adapter-load-ok\n", "")
        _load_adapter_in_fresh_process(Path("/model"), Path("/adapter"))
        arguments, options = run.call_args
        self.assertEqual(sys.executable, arguments[0][0])
        self.assertEqual("-c", arguments[0][1])
        self.assertEqual("", options["env"]["CUDA_VISIBLE_DEVICES"])
        self.assertEqual(300, options["timeout"])

    @patch("llm_lora_playground.tracking.subprocess.run")
    def test_adapter_loader_fails_closed_on_child_error(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "", "load failed")
        with self.assertRaises(ArtifactIntegrityError):
            _load_adapter_in_fresh_process(Path("/model"), Path("/adapter"))


if __name__ == "__main__":
    unittest.main()
