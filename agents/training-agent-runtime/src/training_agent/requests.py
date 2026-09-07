"""Trusted local inputs. Responses cannot grant platform authority."""
import hashlib
from pathlib import Path
from .runner import initial_state

def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def bundle_digest(skill_path):
    """Hash every regular file in the installed Skill bundle, including its path."""
    entry=Path(skill_path)
    if entry.is_symlink(): raise ValueError('Skill entry cannot be a symlink')
    root=entry.resolve(strict=True).parent
    for ancestor in list(root.parents)[:4]:
        if (ancestor/'skill-lock.json').is_file():
            root=ancestor
            break
    checksum=hashlib.sha256()
    paths=list(root.rglob('*'))
    if any(path.is_symlink() for path in paths): raise ValueError('Skill bundle cannot contain symlinks')
    files=sorted(path for path in paths if path.is_file() and '__pycache__' not in path.parts
                 and 'dist' not in path.relative_to(root).parts and path.suffix!='.pyc')
    for path in files:
        if path.is_symlink(): raise ValueError('Skill bundle cannot contain symlinks')
        relative=path.relative_to(root).as_posix().encode()
        content=path.read_bytes()
        checksum.update(len(relative).to_bytes(8,'big')); checksum.update(relative)
        checksum.update(len(content).to_bytes(8,'big')); checksum.update(content)
    return checksum.hexdigest()

def register(store,project_id,campaign_id,revision,workspace,skill_path,lock_path,prompt):
    if store.path(campaign_id).exists(): raise ValueError('Campaign already registered')
    workspace=Path(workspace).resolve(strict=True)
    skill_path=Path(skill_path).resolve(strict=True)
    lock_path=Path(lock_path).resolve(strict=True)
    if not workspace.is_dir() or skill_path.name!='SKILL.md' or not prompt.strip(): raise ValueError('Invalid request')
    state=initial_state(project_id,campaign_id,revision,str(workspace),str(skill_path),digest(lock_path),prompt)
    state.update(skill_digest=bundle_digest(skill_path),runtime_lock_path=str(lock_path))
    store.save(campaign_id,state)

def respond(store,campaign_id,revision,response):
    state=store.load(campaign_id)
    if revision!=state['request_revision'] or state['runner_state'] not in ('awaiting_input','awaiting_approval') or not response.strip():
        raise ValueError('Response does not match a pending request')
    state['user_response']=response; store.save(campaign_id,state)

def cancel(store,campaign_id):
    state=store.load(campaign_id)
    state['cancel_requested']=True; store.save(campaign_id,state)

def verify_binding(state):
    if bundle_digest(state['skill_path'])!=state['skill_digest'] or digest(state['runtime_lock_path'])!=state['runtime_digest']:
        raise ValueError('Runtime or skill changed; refusing resume')

def enqueue(store,campaign_id,command):
    import uuid
    from .runtime.worker import atomic_json
    store.load(campaign_id)
    if command.get('kind') not in ('respond','cancel'): raise ValueError('Unknown command')
    inbox=store.path(campaign_id).parent/'inbox'; inbox.mkdir(mode=0o700,exist_ok=True)
    atomic_json(inbox/(uuid.uuid4().hex+'.json'),command)

def consume_inbox(store,campaign_id):
    import json
    inbox=store.path(campaign_id).parent/'inbox'
    for path in sorted(inbox.glob('*.json')):
        command=json.loads(path.read_text())
        try:
            if command['kind']=='cancel': cancel(store,campaign_id)
            elif command['kind']=='respond': respond(store,campaign_id,command['revision'],command['response'])
            else: raise ValueError('Unknown input')
        except ValueError:
            path.rename(path.with_suffix('.rejected'))
        else: path.unlink()
