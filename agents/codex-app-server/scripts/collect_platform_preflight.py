#!/usr/bin/env python3
"""Read-only platform evidence; never acquires Campaign state or starts a Service."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shlex
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
# Repository use; installed deployment uses its declared Galatea dependency.
import importlib.util
if importlib.util.find_spec('galatea_mcp') is None:
    sys.path.insert(0,str(ROOT.parents[1]/'services/galatea-mcp/src'))
from types import SimpleNamespace
from codex_agent.platform import preflight
from codex_agent.catalog import digest
from codex_agent.release import load_json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--deployment',type=Path,required=True)
    parser.add_argument('--environment-file',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.environment_file:
        path=args.environment_file
        if path.is_symlink() or path.stat().st_mode & 0o007:
            raise ValueError('environment file must be protected')
        for line in path.read_text().splitlines():
            words=shlex.split(line,comments=True)
            if not words:
                continue
            if len(words)!=1 or '=' not in words[0]:
                raise ValueError('unsupported environment file syntax')
            key,value=words[0].split('=',1)
            os.environ[key]=value
    deployment=load_json(args.deployment)
    config=SimpleNamespace(raw={'galatea':{key:deployment[key] for key in
        ('state_root','registry_path','principal','platform')}},project_ids=frozenset(deployment['principal']['project_ids']))
    try:
        result=preflight(config,artifact_index=True)
    except Exception as exc:
        result={'status':'failed','error_type':type(exc).__name__,'training_started':False,'final_test_accessed':False}
    result.update(schema_version='galatea.platform-preflight/v1',deployment_sha256=digest(deployment))
    args.output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with args.output.open('x') as stream:
        os.chmod(args.output,0o600)
        json.dump(result,stream,indent=2)
    print(json.dumps(result,indent=2))
    return 0 if result['status']=='platform-reachable' else 2


if __name__=='__main__':
    raise SystemExit(main())
