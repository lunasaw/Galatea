"""The model wire contract and the handler validation contract stay independent."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from codex_agent.catalog import Catalog
from codex_agent.compatibility import RuntimeContract
from codex_agent.registry import ToolContext, ToolRegistry
from codex_agent.stage0 import GateError
from codex_agent.state import AtomicJsonStore, OperationJournal


class RuntimeCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog.load(ROOT / "config/contracts")
        self.profile = RuntimeContract.load(ROOT / "config/contracts/runtime-compatibility.json", self.catalog)

    def test_new_continuation_and_resume_match_approved_wire_schema(self):
        for name, actions in (("full", {"*"}), ("subset", {"galatea_get_capabilities"})):
            requests = json.loads((ROOT / f"tests/fixtures/codex-0.153.4/{name}-tool-surfaces.json").read_text())
            for index, request in enumerate(requests):
                with self.subTest(principal=name, request=index):
                    self.profile.assert_request(request, actions)

    def test_any_unapproved_schema_change_or_capability_fails(self):
        request = {"tools": self.profile.tools({"galatea_get_capabilities"})}
        for mutate in (
            lambda value: value["tools"].append({"type": "web_search"}),
            lambda value: value["tools"][0]["tools"].clear(),
            lambda value: value["tools"][0]["tools"][0].update(description="changed"),
            lambda value: value["tools"][0]["tools"][0]["parameters"]["properties"]["protocol_version"].update(enum=["v2"]),
        ):
            changed = copy.deepcopy(request)
            mutate(changed)
            with self.assertRaises(GateError):
                self.profile.assert_request(changed, {"galatea_get_capabilities"})

    def test_profile_binds_catalog_and_runtime_bytes(self):
        binding = self.profile.runtime_binding
        self.profile.assert_runtime(binding)
        for key in binding:
            with self.subTest(key=key), self.assertRaises(GateError):
                self.profile.assert_runtime({**binding, key: "changed"})
        metadata = copy.deepcopy(self.catalog.metadata)
        metadata["tools"]["galatea_get_capabilities"]["description"] += " changed"
        with self.assertRaises(GateError):
            RuntimeContract.load(ROOT / "config/contracts/runtime-compatibility.json", Catalog(self.catalog.schemas, metadata))

    def test_arbitrary_principal_subset_is_filtered_without_rewriting(self):
        actions = {"galatea_list_projects", "galatea_get_campaign"}
        tools = self.profile.tools(actions)
        self.assertEqual({tool["name"] for tool in tools[0]["tools"]}, actions)
        self.profile.assert_request({"tools": tools}, actions)
        self.assertEqual(self.profile.tools(set()), [])
        tools[0]["tools"][0]["parameters"] = {}
        with self.assertRaises(GateError):
            self.profile.assert_request({"tools": tools}, actions)

    def test_wire_accepted_invalid_arguments_never_reach_handler(self):
        import jsonschema
        cases = [
            ("galatea_list_projects", {"limit": 101}),
            ("galatea_list_projects", {"limit": 0}),
            ("galatea_inspect_project", {"project_id": "../invalid"}),
            ("galatea_compare_runs", {"project_id": "p", "campaign_id": "c", "run_ids": ["r", "r"]}),
            ("galatea_compare_runs", {"project_id": "p", "campaign_id": "c", "run_ids": [f"r{i}" for i in range(101)]}),
        ]
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            registry = ToolRegistry(self.catalog, OperationJournal(AtomicJsonStore(Path(directory))),
                                    {name: lambda *_: calls.append(True) for name in self.catalog.schemas})
            for index, (name, arguments) in enumerate(cases):
                with self.subTest(tool=name, index=index):
                    wire_schema = self.profile.tools({name})[0]["tools"][0]["parameters"]
                    jsonschema.validate(arguments, wire_schema)
                    context = ToolContext("s", "t", "turn", f"call-{index}", "principal",
                                          frozenset({"p"}), frozenset({"c"}), frozenset({"*"}))
                    result = registry.call(context, name, arguments)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["error"]["category"], "invalid-arguments")
            self.assertEqual(calls, [])
            self.assertFalse((Path(directory) / "tool-calls").exists())
