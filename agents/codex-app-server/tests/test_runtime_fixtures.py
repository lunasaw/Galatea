"""Regression evidence captured from the installed 0.153.4 binary, without prompts."""
import json
from pathlib import Path
import sys
import unittest

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from codex_agent.catalog import Catalog
from codex_agent.compatibility import RuntimeContract
from codex_agent.stage0 import GateError, assert_tool_inventory, assert_tool_surface, file_sha256

FIXTURE = ROOT / "tests/fixtures/codex-0.153.4"


class RuntimeFixtureTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog.load(ROOT / "config/contracts")
        self.provenance = json.loads((FIXTURE / "provenance.json").read_text())

    def test_every_captured_fixture_matches_its_digest(self):
        self.assertEqual(self.provenance["runtime_version"], "codex-cli 0.153.4")
        self.assertEqual(self.provenance["status"], "blocked")
        for name, sha256 in self.provenance["files"].items():
            with self.subTest(name=name):
                self.assertEqual(file_sha256(FIXTURE / name), sha256)

    def test_real_generated_schema_accepts_full_and_subset_catalog(self):
        schema = json.loads((FIXTURE / "schema/ThreadStartParams.json").read_text())
        for actions in ({"*"}, {"galatea_get_capabilities"}):
            jsonschema.validate({"dynamicTools": self.catalog.dynamic_tools(actions), "environments": []}, schema)

    def test_response_fixture_uses_native_input_text_discriminator(self):
        schema = json.loads((FIXTURE / "schema/DynamicToolCallResponse.json").read_text())
        jsonschema.validate({"success": False, "contentItems": [{"type": "inputText", "text": "{}"}]}, schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"success": True, "contentItems": [{"type": "text", "text": "{}"}]}, schema)

    def test_resume_schema_does_not_advertise_dynamic_tools(self):
        schema = json.loads((FIXTURE / "schema/ThreadResumeParams.json").read_text())
        self.assertNotIn("dynamicTools", schema["properties"])

    def test_full_and_subset_requests_match_approved_wire_contract(self):
        profile = RuntimeContract.load(ROOT / "config/contracts/runtime-compatibility.json", self.catalog)
        for name, actions in (("full", {"*"}), ("subset", {"galatea_get_capabilities"})):
            surfaces = json.loads((FIXTURE / f"{name}-tool-surfaces.json").read_text())
            self.assertEqual(len(surfaces), 3)
            for index, surface in enumerate(surfaces):
                with self.subTest(principal=name, request=index):
                    assert_tool_inventory(surface, self.catalog.dynamic_tools(actions))
                    profile.assert_request(surface, actions)
                    with self.assertRaises(GateError):
                        assert_tool_surface(surface, self.catalog.dynamic_tools(actions))
                    self.assertEqual(set(surface), {"tools"})
            self.assertEqual(surfaces[0], surfaces[2])

    def test_original_contract_rejects_arguments_accepted_by_runtime_schema(self):
        request = json.loads((FIXTURE / "full-tool-surfaces.json").read_text())[0]
        observed = {tool["name"]: tool["parameters"] for tool in request["tools"][0]["tools"]}
        cases = [("galatea_list_projects", {"limit": 101}),
                 ("galatea_inspect_project", {"project_id": "../invalid"}),
                 ("galatea_compare_runs", {"project_id": "p", "campaign_id": "c", "run_ids": ["r", "r"]})]
        for name, arguments in cases:
            with self.subTest(tool=name):
                jsonschema.validate(arguments, observed[name])
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(arguments, self.catalog.schemas[name])


if __name__ == "__main__":
    unittest.main()
