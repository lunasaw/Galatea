from pathlib import Path
import json
from galatea_app.contracts import (
    SCHEMAS,
    AGENT_TOOLS,
    READ_ONLY,
    DESCRIPTIONS,
    APP_TOOLS,
)

root = Path(__file__).resolve().parents[1] / "contracts"
root.mkdir(exist_ok=True)
for filename, value in {
    "tools.json": {
        "schema_version": "codex-agent.tools/v1",
        "tools": {k: SCHEMAS[k] for k in sorted(AGENT_TOOLS)},
    },
    "catalog-metadata.json": {
        "schema_version": "codex-agent.catalog/v1",
        "namespace": "galatea_task",
        "description": "Durable tasks, stages and verified evidence",
        "tools": {
            k: {
                "description": DESCRIPTIONS.get(k, k.replace("_", " ")),
                "read_only": k in READ_ONLY,
                "idempotent": True,
                "public_fields": [],
            }
            for k in sorted(AGENT_TOOLS)
        },
    },
    "host-app-tools.json": {
        k: {"read_only": k in READ_ONLY} for k in sorted(APP_TOOLS)
    },
    "app-tools.json": {k: SCHEMAS[k] for k in sorted(APP_TOOLS)},
}.items():
    (root / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
