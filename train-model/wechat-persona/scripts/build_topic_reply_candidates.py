#!/usr/bin/env python3
"""Build a bounded daily-topic pilot without training or external model calls."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from wechat_persona.topic_candidates import build_pilot, load_policy


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/daily-topic-sft.yaml')
    parser.add_argument('--reference-pilot', type=Path,
                        help='Freeze the previous target population; never backfill failures')
    for name in ('source','memory','consent','tokenizer-path','output-root','controlled-root'):
        parser.add_argument('--'+name,type=Path)
    modes=parser.add_mutually_exclusive_group(required=True)
    for name in ('check','plan','execute'):
        modes.add_argument('--'+name,action='store_true')
    args=parser.parse_args()
    if args.check:
        load_policy(args.config)
        print(json.dumps({'status':'config_valid','training_run':False}))
        return 0
    if not all((args.source,args.memory,args.consent,args.tokenizer_path,args.output_root,args.controlled_root)):
        parser.error('plan/execute require all explicit source and output paths')
    try:
        result=build_pilot(source=args.source,memory=args.memory,consent=args.consent,
            config_path=args.config,tokenizer_path=args.tokenizer_path,output_root=args.output_root,
            controlled_root=args.controlled_root,execute=args.execute,reference_pilot=args.reference_pilot)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return 0
    except (ValueError,OSError,KeyError) as exc:
        print(json.dumps({'status':'blocked','error_type':type(exc).__name__,'error':str(exc)}))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
