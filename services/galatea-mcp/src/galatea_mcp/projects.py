"""Administrator-owned registry and immutable input revalidation."""
from __future__ import annotations
import hashlib
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath
from pydantic import ValidationError
from .contracts import Project
from .errors import DomainError
from .state import digest


def relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or '..' in path.parts or '.' in value.split('/')
            or '\\' in value or ':' in value or '%' in value or '\x00' in value):
        raise DomainError('unsafe-path')
    return path


def trusted_file(root: Path, relative: str) -> Path:
    parts = relative_path(relative).parts
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise DomainError('unsafe-path')
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise DomainError('missing-file')
    return path


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


class Registry:
    def __init__(self, raw: dict, objects):
        try:
            if set(raw) != {'schema_version', 'projects'} or raw['schema_version'] != 'galatea.registry/v1':
                raise ValueError('schema')
            parsed = [Project.model_validate(p) for p in raw['projects']]
            if len({p.project_id for p in parsed}) != len(parsed):
                raise ValueError('duplicate')
            self.projects = {p.project_id: p for p in parsed}
        except (ValidationError, ValueError, KeyError) as exc:
            raise DomainError('invalid-registry') from exc
        self.objects = objects

    def get(self, project_id):
        try:
            return self.projects[project_id]
        except KeyError as exc:
            raise DomainError('not-found') from exc

    def verify(self, project_id, config_id, release_id, role):
        p = self.get(project_id)
        try:
            config, release = p.configs[config_id], p.releases[release_id]
        except KeyError as exc:
            raise DomainError('unapproved-input') from exc
        if not release.deadline_enforced:
            raise DomainError('deadline-not-enforced')
        root = Path(p.root)
        if not root.is_absolute() or root.is_symlink():
            raise DomainError('unsafe-path')
        config_path = trusted_file(root, config.path)
        release_path = trusted_file(root, release.path)
        if file_sha(config_path) != config.sha256 or file_sha(release_path) != release.sha256:
            raise DomainError('digest-mismatch')
        try:
            if config_path.stat().st_size > 65536:
                raise ValueError('oversize config')
            raw_config = json.loads(config_path.read_text())
            if raw_config.get('seed') != config.seed:
                raise ValueError('seed')
            with zipfile.ZipFile(release_path) as archive:
                for entry in archive.infolist():
                    relative_path(entry.filename.rstrip('/'))
                    if stat.S_ISLNK(entry.external_attr >> 16):
                        raise ValueError('symlink')
                if len(release.entrypoint) != 2 or release.entrypoint[0] not in {'python', 'python3'}:
                    raise ValueError('fixed python entrypoint required')
                relative_path(release.entrypoint[1])
                if release.entrypoint[1] not in archive.namelist():
                    raise ValueError('missing entrypoint')
                if archive.read(config.path) != config_path.read_bytes():
                    raise ValueError('embedded config differs')
        except (ValueError, KeyError, zipfile.BadZipFile) as exc:
            raise DomainError('invalid-release') from exc
        if role == 'evaluate' and (not p.evaluation_isolated or not p.dataset.holdout_untouched):
            raise DomainError('evaluation-not-ready')
        for name in (['test'] if role == 'evaluate' else ['train', 'validation']):
            verify = self.objects.verify_metadata if name == 'test' else self.objects.verify
            if not verify(p.dataset.views[name].model_dump()):
                raise DomainError('dataset-not-ready')
        frozen = {'schema_version': 'galatea.inputs/v1', 'project': p.model_dump(),
                  'config_id': config_id, 'release_id': release_id, 'role': role}
        return p, config, release, digest(frozen)

    def inspect(self, project_id):
        p = self.get(project_id)
        return {'project_id': p.project_id, 'task': p.task, 'execution_backend': 'ray',
                'objective': p.objective.model_dump(), 'config_ids': list(p.configs),
                'release_ids': list(p.releases),
                'dataset_id': p.dataset.dataset_id, 'dataset_digest': p.dataset.manifest_digest,
                'split_digest': p.dataset.split_digest, 'evaluation_isolated': p.evaluation_isolated,
                'configuration_mode': 'prebuilt', 'pause_resume': False}
