import argparse
import importlib.metadata
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from .state import Store
from .requests import register,enqueue,consume_inbox,verify_binding,digest,bundle_digest
from .platform import SyncPlatform
from .runner import Runner
from .supervisor import Supervisor

def preflight_local(config):
    skill=Path(config['skill_path']).resolve(strict=True)
    workspace=Path(config['workspace']).resolve(strict=True)
    lock=json.loads(Path(config['runtime_lock']).read_text())
    if lock.get('acceptance_status')!='accepted': raise ValueError('Runtime pair acceptance pending; model execution disabled')
    if not workspace.is_dir() or skill.name!='SKILL.md': raise ValueError('Invalid runtime paths')
    expected_skill=lock.get('skill_bundle_sha256')
    if expected_skill!=bundle_digest(skill): raise ValueError('Skill bundle digest mismatch')
    if importlib.metadata.version('openai-codex')!=lock['sdk_version']: raise ValueError('SDK version mismatch')
    configured_bin=config.get('codex_bin','codex')
    runtime_path=Path(configured_bin) if Path(configured_bin).is_absolute() else Path(shutil.which(configured_bin) or '')
    if not runtime_path.is_file(): raise ValueError('Runtime executable not found')
    if lock.get('runtime_artifact_sha256')!=digest(runtime_path): raise ValueError('Runtime artifact digest mismatch')
    result=subprocess.run([str(runtime_path),'--version'],capture_output=True,text=True,check=True,timeout=10)
    if result.stdout.strip()!=lock['runtime_version_output']: raise ValueError('Runtime version mismatch')
    return {'status':'ready','skill_bundle_sha256':bundle_digest(skill)}

def main(argv=None):
    parser=argparse.ArgumentParser(description='Independent bounded Codex training campaign runner')
    parser.add_argument('--config',type=Path,required=True)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('preflight')
    register_parser=sub.add_parser('register')
    register_parser.add_argument('--prompt',required=True)
    register_parser.add_argument('--revision',type=int,required=True)
    respond_parser=sub.add_parser('respond')
    respond_parser.add_argument('--revision',type=int,required=True)
    respond_parser.add_argument('--response',required=True)
    sub.add_parser('cancel')
    run_parser=sub.add_parser('run'); run_parser.add_argument('--once',action='store_true')
    args=parser.parse_args(argv)
    config=json.loads(args.config.read_text())
    store=Store(Path(config['state_root'])); campaign_id=config['campaign_id']
    platform=SyncPlatform(config['mcp'])
    if args.command=='preflight':
        print(json.dumps({'local':preflight_local(config),'mcp':platform.preflight()})); return
    if args.command=='register':
        with store.lock():
            register(store,config['project_id'],campaign_id,args.revision,config['workspace'],config['skill_path'],config['runtime_lock'],args.prompt)
        return
    if args.command=='respond':
        enqueue(store,campaign_id,{'kind':'respond','revision':args.revision,'response':args.response}); return
    if args.command=='cancel':
        # Platform marker precedes interruption; inbox survives Runner downtime.
        platform.cancel(store.load(campaign_id))
        enqueue(store,campaign_id,{'kind':'cancel'}); return
    with store.lock():
        platform.preflight()
        supervisor=Supervisor(store.root/'worker',{**os.environ,'TRAINING_CODEX_BIN':config['codex_bin']},
                              timeout=config.get('turn_timeout_seconds',300),
                              inference_check=lambda:preflight_local(config))
        inbox=store.path(campaign_id).parent/'inbox'
        supervisor.cancel_check=lambda:any(json.loads(path.read_text()).get('kind')=='cancel' for path in inbox.glob('*.json'))
        runner=Runner(store,campaign_id,platform,supervisor)
        while True:
            consume_inbox(store,campaign_id)
            verify_binding(store.load(campaign_id))
            runner.tick(time.time())
            state=store.load(campaign_id)
            if args.once or state['runner_state'] in ('completed','cancelled'): break
            time.sleep(max(1,min(60,state['next_check_at']-time.time())))

if __name__=='__main__': main()
