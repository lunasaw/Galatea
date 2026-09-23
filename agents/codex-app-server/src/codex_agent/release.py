"""Deployment gates bind administrator-reviewed evidence to measured artifacts."""
from __future__ import annotations

import json
import importlib.metadata
import sys
import stat
from pathlib import Path
import subprocess
import tempfile

from .catalog import Catalog, digest
from .compatibility import RuntimeContract
from .stage0 import GateError, file_sha256, package_manifest, require_stage0


def load_json(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)) or path.stat().st_size > 8 * 1024 * 1024:
        raise GateError('unsafe release file')
    return json.loads(path.read_text())


def source_digest(root):
    root = Path(root)
    entries = []
    if root.is_symlink() or not root.is_dir():
        raise GateError('invalid software root')
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if '__pycache__' in relative.parts or path.suffix == '.pyc':
            continue
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise GateError('unsafe software file')
        if path.is_file():
            entries.append({'path': relative.as_posix(), 'sha256': file_sha256(path)})
    return digest(entries)


def software_manifest():
    import galatea_mcp
    packages = sorted((dist.metadata['Name'].lower(), dist.version) for dist in importlib.metadata.distributions())
    return {'host_code_sha256': source_digest(Path(__file__).parent),
            'galatea_code_sha256': source_digest(Path(galatea_mcp.__file__).parent),
            'python_binary_sha256': file_sha256(Path(sys.executable).resolve()),
            'python_version': sys.version.split()[0], 'python_packages_sha256': digest(packages)}


def measure(config):
    catalog = Catalog.load(config.contracts.parent)
    catalog.dynamic_tools(config.actions)
    if config.catalog_metadata != config.contracts.parent / 'catalog-metadata.json':
        raise GateError('catalog metadata must use the bound contract directory')
    compatibility = RuntimeContract.load(config.contracts.parent / 'runtime-compatibility.json', catalog)
    if not config.binary.is_file() or not config.binary.stat().st_mode & 0o111:
        raise GateError('configured runtime is not executable')
    package = load_json(config.runtime_dir / 'codex-package.json')
    if package.get('variant') != 'codex' or package.get('entrypoint') != 'bin/codex' or config.binary != config.runtime_dir / 'bin/codex':
        raise GateError('unsupported runtime package layout')
    static_binding = {'runtime_binary_sha256': file_sha256(config.binary),
        'runtime_package_manifest_sha256': file_sha256(config.runtime_dir / 'codex-package.json'),
        'runtime_package_tree_sha256': package_manifest(config.runtime_dir)['sha256']}
    if any(value != compatibility.runtime_binding[key] for key, value in static_binding.items()):
        raise GateError('runtime bytes differ from the accepted compatibility profile')
    version = subprocess.check_output([str(config.binary), '--version'], text=True, timeout=15).strip()
    if version != config.runtime_version or version != 'codex-cli ' + package['version']:
        raise GateError('runtime version mismatch')
    with tempfile.TemporaryDirectory(prefix='galatea-schema-') as directory:
        subprocess.run([str(config.binary), 'app-server', 'generate-json-schema', '--experimental', '--out', directory],
                       check=True, capture_output=True, timeout=30)
        schema_digest = package_manifest(Path(directory))['sha256']
    manifest = {**software_manifest(), **static_binding, 'codex_version': version,
        'app_server_protocol_schema_sha256': schema_digest, 'dynamic_tool_catalog_sha256': catalog.digest,
        'runtime_compatibility_sha256': compatibility.digest, 'agent_config_sha256': digest(config.raw),
        'codex_config_sha256': file_sha256(config.codex_home / 'config.toml')}
    compatibility.assert_runtime(manifest)
    return manifest


def require_protected_path(path, *, owner_uid=0, boundary=None):
    path = Path(path).absolute()
    boundary = Path(boundary).absolute() if boundary else Path('/')
    if not path.is_relative_to(boundary):
        raise GateError('trusted path escapes deployment root')
    for candidate in (path, *path.parents):
        mode = candidate.lstat()
        if candidate.is_symlink() or mode.st_uid != owner_uid:
            raise GateError('release files must have the trusted administrator owner')
        # A root-owned sticky ancestor (/tmp) cannot replace another owner's
        # directory; release directories themselves may never be shared writable.
        sticky_ancestor = candidate not in (path, boundary) and stat.S_ISDIR(mode.st_mode) and bool(mode.st_mode & stat.S_ISVTX)
        if mode.st_mode & 0o022 and not sticky_ancestor:
            raise GateError('release path is writable by another identity')
        if candidate == boundary:
            return
    raise GateError('trusted path boundary unavailable')


def require_protected_tree(root, *, owner_uid=0, boundary=None):
    root = Path(root)
    require_protected_path(root, owner_uid=owner_uid, boundary=boundary)
    if not root.is_dir():
        raise GateError('trusted software root must be a directory')
    for path in root.rglob('*'):
        mode = path.lstat()
        if not (stat.S_ISREG(mode.st_mode) or stat.S_ISDIR(mode.st_mode)):
            raise GateError('trusted software cannot contain links or special files')
        if mode.st_uid != owner_uid or mode.st_mode & 0o022:
            raise GateError('trusted software is writable by an untrusted identity')


def verify_release(config):
    release = config.raw.get('release', {})
    if set(release) != {'manifest', 'stage0_record'}:
        raise GateError('reviewed release manifest and stage-0 record are required')
    if config.codex_home != config.config_dir / 'codex-home':
        raise GateError('CODEX_HOME must be config-dir/codex-home')
    for path in (config.codex_home, config.state_dir, config.event_dir, config.workspace_dir):
        if not path.is_relative_to(config.config_dir):
            raise GateError('application state must be under its controlled configuration directory')
        if path.exists() and path.stat().st_mode & 0o022:
            raise GateError('application directory is writable by another identity')
    paths = [Path(release[key]) for key in ('manifest', 'stage0_record')]
    paths = [p if p.is_absolute() else config.config_dir / p for p in paths]
    for path in paths:
        require_protected_path(path)
    expected, record = map(load_json, paths)
    if record.get('release_sha256') != digest(expected):
        raise GateError('evidence does not bind this release')
    require_stage0(record, evidence_root=paths[1].parent)
    for check in record['checks'].values():
        require_protected_path(paths[1].parent / check['evidence_path'])
    # A matching digest alone does not prevent replacement between measurement
    # and execution. The runtime and contracts stay administrator-owned.
    require_protected_tree(config.runtime_dir)
    require_protected_tree(config.contracts.parent)
    measured = measure(config)
    if any(expected.get(k) != v for k, v in measured.items()):
        raise GateError('measured release differs from accepted manifest')
    if not isinstance(expected.get('effective_config_sha256'), str) or len(expected['effective_config_sha256']) != 64:
        raise GateError('effective runtime configuration must be accepted')
    source_check = record['checks']['source_provenance']
    provenance = load_json(paths[1].parent / source_check['evidence_path'])
    if (provenance.get('schema_version') != 'galatea.runtime-provenance/v1'
            or provenance.get('status') != 'passed'
            or provenance.get('verification') != 'npm-audit-signatures-and-sigstore-attestations'
            or provenance.get('source_repository') != 'https://github.com/openai/codex'
            or any(provenance.get(key) != expected.get(key) for key in (
                'runtime_binary_sha256', 'runtime_package_tree_sha256'))):
        raise GateError('release lacks matching verified runtime provenance')
    return expected
