"""Render a reviewed config copy; never overwrite the running Host configuration."""

import argparse
import json
from pathlib import Path
from galatea_app.contracts import APP_TOOLS, READ_ONLY

p = argparse.ArgumentParser()
p.add_argument("--host-config", required=True)
p.add_argument("--output", required=True)
p.add_argument("--credential-dir", required=True)
p.add_argument("--service-url", default="http://127.0.0.1:18886")
p.add_argument("--workspace-ref", default="projects")
p.add_argument("--host-id", default="single-owner-host")
p.add_argument("--inputs-root", required=True)
a = p.parse_args()
root = Path(__file__).resolve().parents[1]
config = json.loads(Path(a.host_config).read_text())
base = Path(a.host_config).resolve().parent
for key, value in config["paths"].items():
    if isinstance(value, str):
        config["paths"][key] = str((base / value).resolve())
if config.get("codex", {}).get("binary"):
    config["codex"]["binary"] = str((base / config["codex"]["binary"]).resolve())
credentials = Path(a.credential_dir).resolve()
if config.get("tools", {}).get("providers"):
    raise SystemExit(
        "Existing Providers require an explicit merged catalog; refusing to replace them"
    )
config["tools"] = {
    "catalog_dir": str(root / "contracts"),
    "actions": ["*"],
    "providers": [
        {
            "path": str(root / "src/galatea_app/integrations/codex_provider.py"),
            "options": {
                "service_url": a.service_url,
                "credential_file": str(credentials / "provider.token"),
                "workspace_ref": a.workspace_ref,
                "host_id": a.host_id,
            },
        }
    ],
}
config.setdefault("console", {})["storage"] = {
    "backend": "sqlite",
    "retention": "persistent",
}
roots = config.setdefault("skills", {}).setdefault("roots", [])
workflow_root = str(root.parents[1] / ".codex/skills")
if workflow_root not in roots:
    roots.append(workflow_root)
config["app_integrations"] = {
    "enabled": True,
    "host_id": a.host_id,
    "key_id": "host-key-v1",
    "private_key_file": str(credentials / "host-private.pem"),
    "service_credentials": {"galatea-app": str(credentials / "continuation.token")},
    "servers": {
        "galatea_app": {
            "audience": "galatea-app",
            "resources": ["ui://galatea/workbench/v1"],
            "tools": {k: {"read_only": k in READ_ONLY} for k in APP_TOOLS},
        }
    },
    "workspaces": {
        a.workspace_ref: config["paths"]["workspace_dir"],
        "inputs": str(Path(a.inputs_root).resolve()),
    },
}
Path(a.output).write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
print("Rendered", a.output, "; inspect before starting the Host")
