"""Stage-zero gates must reject incomplete evidence before any deployment."""
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
from codex_agent.stage0 import (
    GateError, REQUIRED_CHECKS, assert_tool_inventory, assert_tool_surface,
    package_manifest, require_stage0, tool_surface_differences,
)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog.load(ROOT / "config/contracts")

    def test_published_contract_is_identical_to_service_contract(self):
        source = ROOT.parents[1] / "services/galatea-mcp/contracts/tools.json"
        self.assertEqual(source.read_bytes(), (ROOT / "config/contracts/tools.json").read_bytes())
        self.assertEqual(len(self.catalog.schemas), 17)

    def test_principal_subset_preserves_exact_schema(self):
        tools = self.catalog.dynamic_tools({"galatea_get_capabilities", "galatea_query_runs"})
        self.assertEqual(tools[0]["name"], "galatea")
        self.assertEqual({tool["name"] for tool in tools[0]["tools"]},
                         {"galatea_get_capabilities", "galatea_query_runs"})
        for tool in tools[0]["tools"]:
            self.assertEqual(tool["inputSchema"], self.catalog.schemas[tool["name"]])
            self.assertTrue(tool["description"])
        self.assertEqual(self.catalog.dynamic_tools(set()), [])
        self.assertEqual(len(self.catalog.dynamic_tools({"*"})[0]["tools"]), 17)

    def test_unknown_actions_fail_instead_of_silently_widening(self):
        with self.assertRaises(ValueError):
            self.catalog.dynamic_tools({"galatea_arbitrary"})

    def test_metadata_must_cover_every_schema_and_have_explicit_annotations(self):
        metadata = copy.deepcopy(self.catalog.metadata)
        del metadata["tools"]["galatea_get_capabilities"]
        with self.assertRaises(ValueError):
            Catalog(self.catalog.schemas, metadata)
        metadata = copy.deepcopy(self.catalog.metadata)
        metadata["tools"]["galatea_get_capabilities"]["read_only"] = "true"
        with self.assertRaises(ValueError):
            Catalog(self.catalog.schemas, metadata)

    def test_description_change_changes_catalog_digest(self):
        metadata = copy.deepcopy(self.catalog.metadata)
        metadata["tools"]["galatea_get_capabilities"]["description"] += " Revised."
        changed = Catalog(self.catalog.schemas, metadata)
        self.assertNotEqual(changed.digest, self.catalog.digest)

    def test_mutating_returned_tools_cannot_change_catalog(self):
        tools = self.catalog.dynamic_tools({"galatea_list_projects"})
        tools[0]["tools"][0]["inputSchema"]["properties"].clear()
        self.assertIn("limit", self.catalog.schemas["galatea_list_projects"]["properties"])


class SurfaceTests(unittest.TestCase):
    def setUp(self):
        self.tools = Catalog.load(ROOT / "config/contracts").dynamic_tools({"galatea_get_capabilities"})
        self.request = {"tools": copy.deepcopy(self.tools)}
        function = self.request["tools"][0]["tools"][0]
        function["parameters"] = function.pop("inputSchema")
        function["strict"] = False

    def test_exact_namespace_schema_description_and_membership(self):
        assert_tool_surface(self.request, self.tools)
        assert_tool_inventory(self.request, self.tools)

    def test_inventory_is_not_schema_evidence(self):
        self.request["tools"][0]["tools"][0]["parameters"] = {"type": "object"}
        assert_tool_inventory(self.request, self.tools)
        with self.assertRaises(GateError):
            assert_tool_surface(self.request, self.tools)
        differences = tool_surface_differences(self.request, self.tools)
        self.assertTrue(any("parameters" in diff["path"] for diff in differences))

    def test_inventory_rejects_other_namespaces_duplicates_and_empty_builtin_namespace(self):
        for extra in [self.request["tools"][0], {"type": "namespace", "name": "skills", "tools": []},
                      {"type": "web_search"}]:
            with self.subTest(extra=extra), self.assertRaises(GateError):
                assert_tool_inventory({"tools": self.request["tools"] + [extra]}, self.tools)

    def test_malformed_request_is_a_gate_failure(self):
        for malformed in [None, [], {"tools": None}, {"tools": [None]}]:
            with self.subTest(malformed=malformed), self.assertRaises(GateError):
                assert_tool_inventory(malformed, self.tools)

    def test_differences_identify_dropped_range_constraint(self):
        self.tools[0]["tools"][0]["inputSchema"]["properties"]["limit"] = {
            "type": "integer", "minimum": 1, "maximum": 100}
        self.request["tools"][0]["tools"][0]["parameters"]["properties"]["limit"] = {"type": "integer"}
        differences = tool_surface_differences(self.request, self.tools)
        self.assertEqual({item["path"] for item in differences}, {
            "/tools/0/tools/0/parameters/properties/limit/maximum",
            "/tools/0/tools/0/parameters/properties/limit/minimum"})

    def test_extra_builtin_even_in_another_namespace_fails(self):
        self.request["tools"].append({"type": "function", "name": "exec_command", "parameters": {}})
        with self.assertRaisesRegex(GateError, "tool-surface"):
            assert_tool_surface(self.request, self.tools)

    def test_schema_drift_and_missing_and_duplicate_and_unknown_tool_types_fail(self):
        cases = []
        changed = copy.deepcopy(self.request)
        changed["tools"][0]["tools"][0]["parameters"] = {"type": "object"}
        cases.append(changed)
        cases.extend([{"tools": []}, {"tools": self.request["tools"] * 2},
                      {"tools": [{"type": "web_search"}]}])
        for request in cases:
            with self.subTest(request=request), self.assertRaises(GateError):
                assert_tool_surface(request, self.tools)


class PackageTests(unittest.TestCase):
    def test_tree_digest_covers_resources_names_and_executable_bits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resource = root / "resource"
            resource.write_bytes(b"resource")
            first = package_manifest(root)
            resource.chmod(0o700)
            second = package_manifest(root)
            self.assertNotEqual(first["sha256"], second["sha256"])
            resource.write_bytes(b"changed")
            third = package_manifest(root)
            self.assertNotEqual(second["sha256"], third["sha256"])
            resource.rename(root / "renamed")
            self.assertNotEqual(third["sha256"], package_manifest(root)["sha256"])
            self.assertEqual(package_manifest(root), package_manifest(root))

    def test_symlinks_and_special_files_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "escape").symlink_to("/etc/passwd")
            with self.assertRaises(GateError):
                package_manifest(root)

    def test_unknown_or_missing_gate_never_passes(self):
        for record in ({}, {"status": "passed"}, {"checks": {"tool_surface": {"status": "passed"}}}):
            with self.subTest(record=record), self.assertRaises(GateError):
                require_stage0(record)

    def test_every_required_gate_needs_proof_even_when_overall_status_says_passed(self):
        record = {"status": "passed", "checks": {
            name: {"status": "passed", "evidence_path": f"{name}.json"} for name in REQUIRED_CHECKS}}
        for name in REQUIRED_CHECKS:
            incomplete = copy.deepcopy(record)
            del incomplete["checks"][name]
            with self.subTest(name=name), self.assertRaises(GateError):
                require_stage0(incomplete)

    def test_malformed_evidence_fails_closed(self):
        for record in [None, [], {"status": "passed", "checks": None},
                       {"status": "passed", "checks": {"catalog": True}}]:
            with self.subTest(record=record), self.assertRaises(GateError):
                require_stage0(record)

    def test_status_strings_without_bound_artifacts_are_not_evidence(self):
        record = {"status": "passed", "checks": {
            name: {"status": "passed", "evidence_path": f"{name}.json"} for name in REQUIRED_CHECKS}}
        with self.assertRaises(GateError):
            require_stage0(record)

    def test_evidence_must_stay_in_root_and_match_digest(self):
        from codex_agent.stage0 import file_sha256
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "proof.json"
            artifact.write_text('{"synthetic_gate_unit_test":true}')
            record = {"status": "passed", "checks": {
                name: {"status": "passed", "evidence_path": "proof.json", "evidence_sha256": file_sha256(artifact)}
                for name in REQUIRED_CHECKS}}
            require_stage0(record, evidence_root=root)
            record["checks"]["catalog"]["evidence_path"] = "../proof.json"
            with self.assertRaises(GateError):
                require_stage0(record, evidence_root=root)
            record["checks"]["catalog"]["evidence_path"] = "proof.json"
            artifact.write_text("changed")
            with self.assertRaises(GateError):
                require_stage0(record, evidence_root=root)


if __name__ == "__main__":
    unittest.main()
