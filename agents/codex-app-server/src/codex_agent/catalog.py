"""Versioned dynamic tool descriptions and principal-filtered input contracts."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class Catalog:
    def __init__(self, schemas: dict, metadata: dict):
        if metadata.get("schema_version") != "galatea.catalog/v1" or metadata.get("namespace") != "galatea":
            raise ValueError("Unsupported catalog metadata")
        if set(schemas) != set(metadata.get("tools", {})) or not schemas:
            raise ValueError("Catalog metadata must cover the entire contract")
        for entry in metadata["tools"].values():
            if not isinstance(entry.get("description"), str) or not entry["description"].strip():
                raise ValueError("An explicit description is required")
            if any(type(entry.get(key)) is not bool for key in ("read_only", "idempotent")):
                raise ValueError("Annotations must be explicit booleans")
            if not isinstance(entry.get("public_fields"), list) or any(
                not isinstance(field, str) for field in entry["public_fields"]
            ):
                raise ValueError("An explicit public field policy is required")
        self.schemas = copy.deepcopy(schemas)
        self.metadata = copy.deepcopy(metadata)
        self.digest = digest({"schemas": schemas, "metadata": metadata})

    @classmethod
    def load(cls, directory: Path) -> Catalog:
        contract = json.loads((directory / "tools.json").read_text())
        if contract.get("schema_version") != "galatea.tools/v1":
            raise ValueError("Unsupported tool contract")
        return cls(contract["tools"], json.loads((directory / "catalog-metadata.json").read_text()))

    def dynamic_tools(self, actions: set[str] | frozenset[str]) -> list[dict]:
        if actions - self.schemas.keys() - {"*"}:
            raise ValueError("Unknown principal action")
        names = sorted(self.schemas if "*" in actions else actions)
        if not names:
            return []
        return [{"type": "namespace", "name": "galatea", "description": self.metadata["description"],
                 "tools": [{"type": "function", "name": name,
                            "description": self.metadata["tools"][name]["description"],
                            "inputSchema": copy.deepcopy(self.schemas[name])} for name in names]}]
