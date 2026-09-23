"""Validate the source/package bindings of cryptographically verified npm provenance.

Statement parsing does not verify a signature. The release collection script uses
npm's registry and Sigstore verifier before calling this policy check.
"""
from __future__ import annotations

import base64
import re
from .stage0 import GateError


def validate_statement(metadata, statement, runtime_version):
    try:
        version = runtime_version + '-linux-x64'
        integrity = metadata['dist']['integrity']
        if metadata['name'] != '@openai/codex' or metadata['version'] != version or not integrity.startswith('sha512-'):
            raise ValueError('package identity')
        archive_sha = base64.b64decode(integrity[7:], validate=True).hex()
        expected = {'name': 'pkg:npm/%40openai/codex@' + version, 'digest': {'sha512': archive_sha}}
        if len(archive_sha) != 128 or statement['subject'] != [expected]:
            raise ValueError('package subject')
        if statement['predicateType'] != 'https://slsa.dev/provenance/v1':
            raise ValueError('provenance format')
        build = statement['predicate']['buildDefinition']
        workflow = build['externalParameters']['workflow']
        if workflow != {'repository': 'https://github.com/openai/codex', 'ref': 'refs/tags/rust-v' + runtime_version,
                        'path': '.github/workflows/rust-release.yml'}:
            raise ValueError('release workflow')
        uri = 'git+https://github.com/openai/codex@refs/tags/rust-v' + runtime_version
        dependencies = [d for d in build['resolvedDependencies'] if d.get('uri') == uri]
        if len(dependencies) != 1 or not re.fullmatch('[a-f0-9]{40}', dependencies[0]['digest']['gitCommit']):
            raise ValueError('source revision')
        return {'source_repository': workflow['repository'], 'source_commit': dependencies[0]['digest']['gitCommit'],
                'source_ref': workflow['ref'], 'archive_sha512': archive_sha, 'workflow': workflow['path']}
    except (KeyError, TypeError, ValueError) as exc:
        raise GateError('runtime source provenance binding mismatch') from exc
