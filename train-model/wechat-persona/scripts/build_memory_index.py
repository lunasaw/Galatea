#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wechat_persona.runtime import load_project_config, validate_project_config

def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only memory index planning boundary")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    config = load_project_config(args.config)
    errors = validate_project_config(config)
    rag = config.get("rag", {})
    backend = rag.get("backend")
    if backend not in {"bm25", "embedding"}:
        errors.append("rag.backend must be bm25 or embedding")
    if not rag.get("owner_scope_required"):
        errors.append("rag.owner_scope_required must be true")
    if backend == "embedding" and (not rag.get("embedding_model_revision") or not rag.get("pooling")):
        errors.append("embedding indexes require immutable model revision and pooling")
    print(json.dumps({
        "status": "planned" if not errors else "blocked",
        "errors": errors,
        "index_backend": backend,
        "owner_scope_required": bool(rag.get("owner_scope_required")),
        "exclude_high_sensitivity": bool(rag.get("exclude_high_sensitivity", True)),
        "requires_status": ["FORMAL_DATASET_READY", "MEMORY_ONLY_SNAPSHOT_CONFIRMED"],
        "will_write_index": False,
        "will_create_mlflow_run": False,
    }, sort_keys=True))
    return 0 if not errors else 2
if __name__ == "__main__": raise SystemExit(main())
