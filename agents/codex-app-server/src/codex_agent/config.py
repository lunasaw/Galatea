from __future__ import annotations

from dataclasses import dataclass, field
import copy
import os
from pathlib import Path

from .catalog import digest
from .state import identifier


@dataclass(frozen=True)
class AgentConfig:
    config_dir: Path
    codex_home: Path
    runtime_dir: Path
    state_dir: Path
    event_dir: Path
    workspace_dir: Path
    contracts: Path
    catalog_metadata: Path
    binary: Path
    runtime_version: str
    principal_id: str
    project_ids: frozenset[str]
    campaign_ids: frozenset[str]
    actions: frozenset[str]
    max_active_turns: int = 4
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: dict, config_dir: Path):
        if raw.get("schema_version") != "codex-agent/v1":
            raise ValueError("unsupported config schema")
        if raw.keys() - {"schema_version", "paths", "codex", "agent", "galatea", "console", "release"}:
            raise ValueError("unknown configuration field")
        config_dir = config_dir.absolute()
        if any(p.is_symlink() for p in (config_dir, *config_dir.parents)):
            raise ValueError("unsafe configuration directory")
        paths, codex = raw.get("paths", {}), raw.get("codex", {})
        if paths.keys() != {"codex_home", "runtime_dir", "state_dir", "event_dir", "workspace_dir", "contracts", "catalog_metadata"}:
            raise ValueError("invalid paths fields")
        if codex.keys() - {"binary", "runtime_version", "app_server_args", "model_api_key_file"}:
            raise ValueError("unknown runtime field")
        if codex.get("app_server_args", ["--listen", "stdio://", "--strict-config"]) != ["--listen", "stdio://", "--strict-config"]:
            raise ValueError("runtime arguments are frozen")
        if raw.get('agent', {}).keys() - {'id', 'max_active_turns'}:
            raise ValueError('unknown agent field')
        console = raw.get('console', {})
        if console.keys() - {'token_file', 'allowed_origins'}:
            raise ValueError('unknown Console field')
        if not isinstance(console.get('allowed_origins', []), list) or any(
                not isinstance(value, str) or not value.startswith(('http://', 'https://'))
                for value in console.get('allowed_origins', [])):
            raise ValueError('invalid Console origins')
        maximum = raw.get("agent", {}).get("max_active_turns", 4)
        if type(maximum) is not int or not 1 <= maximum <= 32:
            raise ValueError("max_active_turns must be between 1 and 32")
        principal = raw.get("galatea", {}).get("principal", {})
        if principal.keys() - {'principal_id', 'actions', 'project_ids', 'campaign_ids'}:
            raise ValueError('unknown principal field')
        def path(key):
            value = paths.get(key)
            if not isinstance(value, str):
                raise ValueError(f"missing paths.{key}")
            candidate = Path(value)
            candidate = candidate if candidate.is_absolute() else config_dir / candidate
            if ".." in candidate.parts or any(p.is_symlink() for p in (candidate, *candidate.parents)):
                raise ValueError("unsafe configured path")
            return candidate.absolute()
        binary = Path(codex.get("binary", ""))
        binary = (binary if binary.is_absolute() else config_dir / binary).absolute()
        if ".." in binary.parts or any(p.is_symlink() for p in (binary, *binary.parents)):
            raise ValueError("unsafe binary path")
        for key in ("actions", "project_ids", "campaign_ids"):
            values = principal.get(key, [])
            if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
                raise ValueError("principal scopes must be string lists")
        identifier(principal.get("principal_id"))
        actions = frozenset(principal.get("actions", []))
        if not principal.get("principal_id") or not actions:
            raise ValueError("principal_id and actions are required")
        return cls(config_dir, path("codex_home"), path("runtime_dir"), path("state_dir"), path("event_dir"),
                   path("workspace_dir"), path("contracts"), path("catalog_metadata"), binary,
                   str(codex.get("runtime_version", "")), str(principal["principal_id"]),
                   frozenset(principal.get("project_ids", [])), frozenset(principal.get("campaign_ids", [])), actions,
                   maximum, copy.deepcopy(raw))

    def effective_digest(self, effective_config: dict):
        return digest({"effective_config": effective_config, "config_dir": str(self.config_dir), "codex_home": str(self.codex_home)})

    def child_environment(self):
        environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
        key_file = self.raw.get("codex", {}).get("model_api_key_file")
        if key_file:
            key_path = Path(key_file)
            if not key_path.is_absolute() or key_path.is_symlink() or key_path.stat().st_mode & 0o077:
                raise ValueError("model key file must be an absolute protected file")
            environment["OPENAI_API_KEY"] = key_path.read_text().strip()
        environment.update(HOME=str(self.config_dir / "codex-process-home"), CODEX_HOME=str(self.codex_home))
        return environment
