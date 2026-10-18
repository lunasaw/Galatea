"""Independent source startup for the generic Codex host."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib

from .catalog import digest
from .stage0 import GateError, file_sha256
from .runtime_version import VERSION, RUNTIME_VERSION, PROTOCOL_PATH


def toml_value(value):
    if isinstance(value, dict):
        return '{' + ', '.join(f'{json.dumps(k)} = {toml_value(v)}' for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(toml_value(v) for v in value) + ']'
    if isinstance(value, (str, bool, int, float)):
        return json.dumps(value)
    raise ValueError('unsupported model provider setting')


def source_manifest(config):
    version = subprocess.check_output([str(config.binary), '--version'], text=True, timeout=15).strip()
    if version != config.runtime_version or version != RUNTIME_VERSION:
        raise GateError(f'runtime must match the supported Codex {VERSION} protocol')
    catalog = config.catalog()
    catalog.dynamic_tools(config.actions)
    from .release import extension_manifest
    return {
        'extensions_sha256': extension_manifest(config),
        'runtime_binary_sha256': file_sha256(config.binary),
        'runtime_compatibility_sha256': digest({'protocol': file_sha256(PROTOCOL_PATH), 'catalog': catalog.digest}),
        'effective_config_sha256': '',
        'agent_config_sha256': digest(config.raw),
        'codex_config_sha256': file_sha256(config.codex_home / 'config.toml'),
    }


def prepare(project: Path, binary: Path):
    version = subprocess.check_output([str(binary), '--version'], text=True, timeout=15).strip()
    if version != RUNTIME_VERSION:
        raise GateError(f'runtime must match the supported Codex {VERSION} protocol')
    directory = project / '.local'
    directory.mkdir(exist_ok=True, mode=0o700)
    path = directory / 'agent.json'
    if path.exists():
        from .config import AgentConfig
        AgentConfig.from_dict(json.loads(path.read_text()), directory)
    home = directory / 'codex-home'
    home.mkdir(exist_ok=True, mode=0o700)
    auth_directory = Path(os.environ.get('CODEX_AGENT_AUTH_DIR', str(Path.home() / '.codex'))).expanduser()
    user_config = auth_directory / 'config.toml'
    settings = tomllib.loads(user_config.read_text()) if user_config.is_file() else {}
    config_file = home / 'config.toml'
    if not config_file.exists():
        template = (project / 'config/codex.example.toml').read_text()
        model = os.environ.get('CODEX_AGENT_MODEL', settings.get('model', 'gpt-6-astra'))
        template = template.replace('"REPLACE_WITH_APPROVED_MODEL"', json.dumps(model))
        provider = settings.get('model_provider', 'openai')
        template = template.replace('model_provider = "openai"', 'model_provider = ' + json.dumps(provider))
        prefix = 'cli_auth_credentials_store = "file"\n'
        for key in ('openai_base_url', 'model_reasoning_effort', 'service_tier'):
            if key in settings:
                prefix += f'{key} = {json.dumps(settings[key])}\n'
        if os.environ.get('OPENAI_BASE_URL'):
            prefix += f'openai_base_url = {json.dumps(os.environ["OPENAI_BASE_URL"])}\n' if 'openai_base_url' not in settings else ''
        provider_settings = settings.get('model_providers', {}).get(provider)
        if provider_settings:
            template += f'\n[model_providers.{json.dumps(provider)}]\n'
            for key, value in provider_settings.items():
                template += f'{json.dumps(key)} = {toml_value(value)}\n'
        # Keep dynamic tools in the direct format used by this Host, including
        # when the selected model normally defaults to code-mode tools.
        bundled = subprocess.check_output([str(binary), 'debug', 'models', '--bundled'],
            env={**os.environ, 'CODEX_HOME': str(home)}, text=True, timeout=30)
        models = json.loads(bundled)['models']
        for entry in models:
            entry['tool_mode'] = 'direct'
        catalog = home / 'models.json'
        catalog.write_text(json.dumps({'models': models}))
        prefix += f'model_catalog_json = {json.dumps(str(catalog))}\n'
        config_file.write_text(prefix + template)
        config_file.chmod(0o600)
    auth_file = home / 'auth.json'
    if not auth_file.exists() and (auth_directory / 'auth.json').is_file():
        shutil.copyfile(auth_directory / 'auth.json', auth_file)
        auth_file.chmod(0o600)
    key = os.environ.get('OPENAI_API_KEY')
    key_file = directory / 'model-api-key'
    if key:
        key_file.write_text(key)
        key_file.chmod(0o600)
    path = directory / 'agent.json'
    if not path.exists():
        raw = json.loads((project / 'config/agent.example.json').read_text())
        raw.pop('release', None)
        raw['paths']['runtime_dir'] = str(binary.parent.parent)
        raw['codex']['binary'] = str(binary)
        raw['codex'].pop('model_api_key_file', None)
        raw['console']['host'] = '0.0.0.0'
        raw['console']['storage'] = {'backend': 'sqlite', 'retention': 'persistent'}
        raw['paths']['workspace_dir'] = os.environ.get('CODEX_AGENT_WORKSPACE', str(project.parents[1]))
        path.write_text(json.dumps(raw, indent=2) + '\n')
        path.chmod(0o600)
    raw = json.loads(path.read_text())
    from .config import AgentConfig
    AgentConfig.from_dict(raw, directory)
    previous = json.loads(path.read_text())
    raw['paths']['runtime_dir'] = str(binary.parent.parent)
    raw['codex'].update(binary=str(binary), runtime_version=RUNTIME_VERSION)
    if key_file.exists():
        raw['codex']['model_api_key_file'] = str(key_file)
    if raw != previous:
        backup = directory / f'agent.before-runtime-{VERSION}.json'
        if not backup.exists():
            backup.write_text(json.dumps(previous, indent=2) + '\n')
            backup.chmod(0o600)
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(raw, indent=2) + '\n')
        temporary.chmod(0o600)
        temporary.replace(path)
    if not auth_file.exists() and not raw['codex'].get('model_api_key_file'):
        raise SystemExit('Set OPENAI_API_KEY, or log in with Codex, then run start.sh again.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('project', type=Path)
    parser.add_argument('binary', type=Path)
    args = parser.parse_args()
    prepare(args.project.resolve(), args.binary.resolve())
