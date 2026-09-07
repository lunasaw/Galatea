#!/usr/bin/env python3
"""Build-time exporter. It is the only source-tree import in this package."""
from __future__ import annotations
import importlib.util
import json
import sys
from pathlib import Path


def export(contract_source, destination):
    spec = importlib.util.spec_from_file_location("galatea_mcp_contracts", contract_source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = {"protocol_version": "galatea.tools/v1", "tools": [
        {"name": name, "input_schema": schema} for name, schema in sorted(module.TOOLS.items())
    ]}
    destination.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: export_contracts.py CONTRACTS_PY DESTINATION_JSON")
    export(Path(sys.argv[1]), Path(sys.argv[2]))
