"""Cross-release contract synchronization belongs to repository acceptance."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TrainingSkillContractTests(unittest.TestCase):
    def test_exported_tool_contract_matches_mcp_release(self):
        package = ROOT / "packages/galatea-training-skills"
        spec = importlib.util.spec_from_file_location(
            "skill_contract_exporter", package / "scripts/export_contracts.py"
        )
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)
        source = ROOT / "services/galatea-mcp/src/galatea_mcp/contracts.py"
        with tempfile.TemporaryDirectory() as directory:
            exported = Path(directory) / "tools.json"
            exporter.export(source, exported)
            self.assertEqual(
                json.loads(exported.read_text()),
                json.loads((package / "contracts/tools.json").read_text()),
            )
