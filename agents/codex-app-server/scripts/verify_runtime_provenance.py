#!/usr/bin/env python3
"""Collect npm/Sigstore verification and compare the published artifact to a runtime.

Downloads only into a new private output directory. Package lifecycle scripts are
never executed. No Codex/Core source commit is inferred from a local checkout.
"""
from __future__ import annotations
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from codex_agent.catalog import digest
from codex_agent.provenance import validate_statement
from codex_agent.stage0 import GateError, file_sha256, package_manifest


def collect(package: Path, output: Path):
    output.mkdir(parents=True,mode=0o700,exist_ok=False)
    info=json.loads((package/'codex-package.json').read_text())
    if info.get('target')!='x86_64-unknown-linux-musl' or info.get('variant')!='codex':
        raise GateError('only the accepted Linux x64 unified package is supported')
    version=info['version']; published=version+'-linux-x64'
    url='https://registry.npmjs.org/@openai/codex/'+published
    with urllib.request.urlopen(url,timeout=30) as response:
        metadata=json.load(response)
    (output/'npm-metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
    # No user npmrc, install scripts or audit remediation; npm verifies registry
    # signatures and Sigstore attestations using its own public trust roots.
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','LANG','SSL_CERT_FILE'}}
    (output/'empty-user.npmrc').touch()
    (output/'empty-global.npmrc').touch()
    env.update(NPM_CONFIG_USERCONFIG=str(output/'empty-user.npmrc'),
               NPM_CONFIG_GLOBALCONFIG=str(output/'empty-global.npmrc'))
    def npm(*args):
        result=subprocess.run(['npm',*args,'--registry=https://registry.npmjs.org'],cwd=output,env=env,
                              text=True,capture_output=True,timeout=180)
        if result.returncode:
            (output/'npm-error.log').write_text(result.stderr)
            raise GateError('npm verification failed; inspect private evidence')
        return result.stdout
    (output/'install.log').write_text(npm('install','--ignore-scripts','--omit=optional','--save-exact','@openai/codex@'+published))
    raw=npm('audit','signatures','--json','--include-attestations')
    (output/'verified-attestations.json').write_text(raw)
    audit=json.loads(raw)
    if audit.get('invalid') or audit.get('missing') or len(audit.get('verified',[]))!=1:
        raise GateError('missing/invalid package signature or attestation')
    verified=audit['verified'][0]
    if (verified.get('name'),verified.get('version'),verified.get('registry')) != ('@openai/codex',published,'https://registry.npmjs.org/'):
        raise GateError('verified package identity mismatch')
    bundles=[a for a in verified.get('attestationBundles',[]) if a.get('predicateType')=='https://slsa.dev/provenance/v1']
    if len(bundles)!=1:
        raise GateError('verified source provenance absent')
    statement=json.loads(base64.b64decode(bundles[0]['bundle']['dsseEnvelope']['payload'],validate=True))
    binding=validate_statement(metadata,statement,version)
    downloaded=output/'node_modules/@openai/codex/vendor/x86_64-unknown-linux-musl'
    installed_tree=package_manifest(package)
    if installed_tree!=package_manifest(downloaded):
        raise GateError('installed runtime differs from signed registry artifact')
    report={'schema_version':'galatea.runtime-provenance/v1','status':'passed',**binding,
            'verification':'npm-audit-signatures-and-sigstore-attestations',
            'codex_version':'codex-cli '+version,'runtime_package_tree_sha256':installed_tree['sha256'],
            'runtime_binary_sha256':file_sha256(package/'bin/codex'),
            'npm_version':subprocess.check_output(['npm','--version'],text=True).strip(),
            'evidence':{name:file_sha256(output/name) for name in
                        ('npm-metadata.json','verified-attestations.json','package-lock.json')}}
    (output/'source-provenance.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(collect(args.package.resolve(),args.output.absolute()),indent=2))


if __name__=='__main__':
    main()
