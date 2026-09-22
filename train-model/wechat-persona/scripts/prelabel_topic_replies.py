#!/usr/bin/env python3
"""Review a train-only daily-topic pilot with a bounded external model budget."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from wechat_persona.topic_review import audit_pilot


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('pilot','consent','output-root','controlled-root'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--base-url',default=os.environ.get('OPENAI_BASE_URL'))
    parser.add_argument('--auth-file',type=Path,default=Path.home()/'.codex/auth.json')
    parser.add_argument('--authorization-reference',required=True)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--plan',action='store_true'); modes.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    if not args.base_url:
        parser.error('explicit base URL or OPENAI_BASE_URL required')
    try:
        result=audit_pilot(pilot=args.pilot,consent=args.consent,output_root=args.output_root,
            controlled_root=args.controlled_root,base_url=args.base_url,auth_file=args.auth_file,
            authorization_reference=args.authorization_reference,execute=args.execute)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return 0 if result['status'] in {'complete','planned'} else 2
    except (ValueError,OSError,KeyError) as exc:
        print(json.dumps({'status':'blocked','error_type':type(exc).__name__,'error':str(exc)}))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
