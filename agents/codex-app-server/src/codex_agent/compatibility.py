"""Reviewed wire schema for a specific runtime; never used to validate calls."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .catalog import Catalog, digest
from .stage0 import GateError, assert_tool_inventory

BINDINGS = frozenset({"codex_version", "runtime_binary_sha256", "runtime_package_tree_sha256",
                      "runtime_package_manifest_sha256", "app_server_protocol_schema_sha256"})


class RuntimeContract:
    def __init__(self, profile: dict, catalog: Catalog):
        if profile.get("schema_version") != "galatea.runtime-compatibility/v1":
            raise GateError("Unsupported runtime compatibility profile")
        if profile.get("host_catalog_sha256") != catalog.digest:
            raise GateError("Compatibility profile does not bind the current host catalog")
        binding = profile.get("runtime_binding", {})
        if set(binding) != BINDINGS or not all(isinstance(value, str) and value for value in binding.values()):
            raise GateError("Compatibility profile needs a complete runtime binding")
        wire = profile.get("model_tools")
        assert_tool_inventory({"tools": wire}, catalog.dynamic_tools({"*"}))
        if profile.get("model_tools_sha256") != digest(wire):
            raise GateError("Compatibility wire schema digest mismatch")
        if wire[0].get("description") != catalog.metadata["description"]:
            raise GateError("Compatibility namespace description mismatch")
        for tool in wire[0]["tools"]:
            if (tool.get("description") != catalog.metadata["tools"][tool["name"]]["description"]
                    or not isinstance(tool.get("parameters"), dict) or tool.get("strict") is not False):
                raise GateError("Compatibility function metadata mismatch")
        self.catalog = catalog
        self._profile = copy.deepcopy(profile)
        self.runtime_binding = copy.deepcopy(binding)
        self.digest = digest(profile)

    @classmethod
    def load(cls, path: Path, catalog: Catalog) -> RuntimeContract:
        try:
            return cls(json.loads(path.read_text()), catalog)
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise GateError("Invalid compatibility profile") from exc

    def tools(self, actions: set[str] | frozenset[str]) -> list[dict]:
        # Reuse principal validation; filter the frozen model schema without
        # attempting to imitate Codex's schema normalizer in Host code.
        dynamic = self.catalog.dynamic_tools(actions)
        if not dynamic:
            return []
        allowed = {tool["name"] for tool in dynamic[0]["tools"]}
        wire = copy.deepcopy(self._profile["model_tools"])
        wire[0]["tools"] = [tool for tool in wire[0]["tools"] if tool["name"] in allowed]
        return wire

    def assert_request(self, request: dict, actions: set[str] | frozenset[str]) -> None:
        assert_tool_inventory(request, self.catalog.dynamic_tools(actions))
        if request.get("tools") != self.tools(actions):
            raise GateError("Model wire schema differs from the approved compatibility profile")

    def assert_runtime(self, manifest: dict) -> None:
        if any(manifest.get(key) != value for key, value in self.runtime_binding.items()):
            raise GateError("Runtime differs from the approved compatibility binding")
