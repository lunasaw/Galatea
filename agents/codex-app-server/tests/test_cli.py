from pathlib import Path
import json
import os
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from codex_agent.cli import check
from codex_agent.stage0 import GateError


class CLITests(unittest.TestCase):
    def test_check_is_read_only_and_requires_accepted_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("home", "runtime", "state", "events", "workspace"):
                (root / name).mkdir()
            binary = root / "runtime/bin/codex"
            binary.parent.mkdir()
            binary.write_text("#!/bin/sh\nexit 0\n")
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
            config = {"schema_version": "codex-agent/v1", "paths": {
                "codex_home": "home", "runtime_dir": "runtime", "state_dir": "state", "event_dir": "events",
                "workspace_dir": "workspace", "contracts": str(ROOT / "config/contracts/tools.json"),
                "catalog_metadata": str(ROOT / "config/contracts/catalog-metadata.json")},
                "codex": {"binary": str(binary), "runtime_version": "codex-cli test"},
                "galatea": {"principal": {"principal_id": "p", "actions": ["galatea_get_capabilities"]}}}
            path = root / "agent.json"
            path.write_text(json.dumps(config))
            with self.assertRaises(GateError):
                check(path)
            self.assertEqual(sorted(p.name for p in root.iterdir()), sorted(("home", "runtime", "state", "events", "workspace", "agent.json")))
            config["codex"]["runtime_version"] = "REPLACE_WITH_ACCEPTED_VERSION"
            path.write_text(json.dumps(config))
            with self.assertRaises(GateError):
                check(path)


if __name__ == "__main__": unittest.main()
