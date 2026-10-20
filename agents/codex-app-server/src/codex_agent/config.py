"""Configuration for the generic native Codex host (no business defaults)."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import os
from pathlib import Path
import re

from .catalog import Catalog, digest
from .state import identifier


def _object(value, allowed, label):
    if not isinstance(value, dict) or value.keys() - allowed:
        raise ValueError(f'invalid {label} fields')
    return value


@dataclass(frozen=True)
class AgentConfig:
    config_dir: Path
    codex_home: Path
    runtime_dir: Path
    state_dir: Path
    workspace_dir: Path
    binary: Path
    runtime_version: str
    principal_id: str
    actions: frozenset[str]
    catalog_dir: Path | None = None
    compatibility_profile: Path | None = None
    skill_roots: tuple[Path, ...] = ()
    providers: tuple[dict, ...] = field(default=(), repr=False)
    console_host: str = '0.0.0.0'
    console_port: int = 18880
    max_active_turns: int = 4
    image_generation: dict = field(default_factory=dict, repr=False)
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, raw: dict, config_dir: Path):
        _object(raw, {'schema_version', 'paths', 'codex', 'agent', 'skills', 'tools', 'console', 'release', 'image_generation', 'app_integrations'}, 'configuration')
        if raw.get('schema_version') != 'codex-agent/v2':
            raise ValueError('unsupported config schema; create a codex-agent/v2 configuration')
        config_dir = config_dir.absolute()
        def path(value):
            if not isinstance(value, str) or not value.strip():
                raise ValueError('configured paths must be nonempty strings')
            candidate = Path(value)
            candidate = candidate if candidate.is_absolute() else config_dir / candidate
            if '..' in candidate.parts or any(p.is_symlink() for p in (candidate, *candidate.parents)):
                raise ValueError('unsafe configured path')
            return candidate.absolute()
        path(str(config_dir))
        paths = _object(raw.get('paths', {}), {'codex_home', 'runtime_dir', 'state_dir', 'workspace_dir'}, 'paths')
        if set(paths) != {'codex_home', 'runtime_dir', 'state_dir', 'workspace_dir'}:
            raise ValueError('all four runtime paths are required')
        codex = _object(raw.get('codex', {}), {'binary', 'runtime_version', 'model_api_key_file', 'inherit_env', 'compatibility_profile'}, 'runtime')
        if not isinstance(codex.get('runtime_version'), str) or not codex['runtime_version']:
            raise ValueError('runtime_version is required')
        names = codex.get('inherit_env', [])
        if not isinstance(names, list) or any(not isinstance(n, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', n) for n in names):
            raise ValueError('inherit_env must be a list of environment variable names')
        if set(names) & {'HOME', 'CODEX_HOME'}:
            raise ValueError('HOME and CODEX_HOME are controlled by the Host')
        if codex.get('model_api_key_file'):
            if not isinstance(codex['model_api_key_file'], str) or not Path(codex['model_api_key_file']).is_absolute():
                raise ValueError('model key file must be absolute')
            path(codex['model_api_key_file'])
        agent = _object(raw.get('agent', {}), {'id', 'max_active_turns'}, 'agent')
        principal = identifier(agent.get('id', 'codex-agent'))
        maximum = agent.get('max_active_turns', 4)
        if type(maximum) is not int or not 1 <= maximum <= 32:
            raise ValueError('max_active_turns must be between 1 and 32')
        skills = _object(raw.get('skills', {}), {'roots'}, 'skills')
        roots = skills.get('roots', [])
        if not isinstance(roots, list):
            raise ValueError('skills.roots must be a list')
        tools = _object(raw.get('tools', {}), {'catalog_dir', 'actions', 'providers'}, 'tools')
        actions = tools.get('actions', ['*'] if tools.get('catalog_dir') else [])
        if not isinstance(actions, list) or any(not isinstance(a, str) or not a for a in actions):
            raise ValueError('tools.actions must be a string list')
        providers = tools.get('providers', [])
        if not isinstance(providers, list):
            raise ValueError('tools.providers must be a list')
        parsed = []
        for provider in providers:
            _object(provider, {'path', 'factory', 'options'}, 'tool provider')
            factory = provider.get('factory', 'create_provider')
            if not isinstance(factory, str) or not factory.isidentifier():
                raise ValueError('provider factory must be a Python identifier')
            if not isinstance(provider.get('options', {}), dict):
                raise ValueError('provider options must be an object')
            parsed.append({'path': path(provider.get('path')), 'factory': factory, 'options': copy.deepcopy(provider.get('options', {}))})
        if bool(tools.get('catalog_dir')) != bool(parsed) or (actions and not parsed):
            raise ValueError('custom tools require a catalog and configured providers together')
        console = _object(raw.get('console', {}), {'host', 'port', 'storage'}, 'Console')
        storage = _object(console.get('storage', {}), {'backend', 'retention'}, 'Console storage')
        backend = storage.get('backend', 'memory')
        if backend not in {'memory', 'sqlite'}:
            raise ValueError('unsupported Console storage backend')
        retention = 'persistent' if backend == 'sqlite' else 'process_lifetime'
        if storage.get('retention', retention) != retention:
            raise ValueError('Console retention does not match storage backend')
        host, port = console.get('host', '0.0.0.0'), console.get('port', 18880)
        if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]+', host):
            raise ValueError('invalid Console host')
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('invalid Console port')
        _object(raw.get('release', {}), {'manifest', 'stage0_record'}, 'release')
        from .image_generation import configuration_defaults
        images = configuration_defaults(raw.get('image_generation', {}), path)
        from .app_integrations import validate_config
        validate_config(raw.get('app_integrations', {}))
        if raw.get('app_integrations', {}).get('enabled') and backend != 'sqlite':
            raise ValueError('app integrations require persistent Console storage')
        return cls(config_dir=config_dir, **{k: path(v) for k, v in paths.items()},
                   binary=path(codex.get('binary')), runtime_version=codex['runtime_version'],
                   principal_id=principal, actions=frozenset(actions),
                   catalog_dir=path(tools['catalog_dir']) if tools.get('catalog_dir') else None,
                   compatibility_profile=path(codex['compatibility_profile']) if codex.get('compatibility_profile') else None,
                   skill_roots=tuple(path(root) for root in roots), providers=tuple(parsed),
                   console_host=host, console_port=port, max_active_turns=maximum,
                   image_generation=images, raw=copy.deepcopy(raw))

    def catalog(self):
        catalog = Catalog.load(self.catalog_dir) if self.catalog_dir else Catalog.empty()
        catalog.dynamic_tools(self.actions)
        return catalog

    def effective_digest(self, effective_config: dict):
        return digest({'effective_config': effective_config, 'config_dir': str(self.config_dir), 'codex_home': str(self.codex_home)})

    def child_environment(self):
        environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
        environment.update({name: os.environ[name] for name in self.raw.get('codex', {}).get('inherit_env', []) if name in os.environ})
        key_file = self.raw.get('codex', {}).get('model_api_key_file')
        if key_file:
            key_path = Path(key_file)
            if (not key_path.is_file() or any(p.is_symlink() for p in (key_path, *key_path.parents))
                    or key_path.stat().st_mode & 0o077):
                raise ValueError('model key file must be an absolute protected file')
            environment['OPENAI_API_KEY'] = key_path.read_text().strip()
            if not environment['OPENAI_API_KEY']:
                raise ValueError('model key file must not be empty')
        environment.update(HOME=str(self.config_dir / 'codex-process-home'), CODEX_HOME=str(self.codex_home))
        return environment
