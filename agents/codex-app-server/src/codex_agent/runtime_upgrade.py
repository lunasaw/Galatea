"""Migrate idle source-mode sessions across the tested 0.154 -> 0.163 upgrade."""
import copy
import json
from pathlib import Path
import subprocess

from .catalog import digest
from .config import AgentConfig
from .runtime_version import VERSION, RUNTIME_VERSION
from .stage0 import file_sha256


def migrate_source_sessions(config, store, bindings):
    backup = config.config_dir / f'agent.before-runtime-{VERSION}.json'
    if not backup.is_file():
        return
    previous = json.loads(backup.read_text())
    if previous.get('release') or previous.get('codex', {}).get('runtime_version') != 'codex-cli 0.154.0':
        return
    expected = copy.deepcopy(previous)
    expected['paths']['runtime_dir'] = config.raw['paths']['runtime_dir']
    expected['codex'].update(binary=config.raw['codex']['binary'], runtime_version=RUNTIME_VERSION)
    if expected != config.raw:
        return  # A runtime upgrade cannot authorize other configuration changes.
    old = AgentConfig.from_dict(previous, config.config_dir)
    if not old.binary.is_file():
        return
    version = subprocess.check_output([str(old.binary), '--version'], text=True, timeout=15).strip()
    if version != old.runtime_version:
        return
    old_protocol = Path(__file__).parent / 'protocol/0.154.0.json'
    old_bindings = {**bindings, 'runtime_binary_sha256': file_sha256(old.binary),
        'runtime_compatibility_sha256': digest({'protocol': file_sha256(old_protocol), 'catalog': config.catalog().digest}),
        'agent_config_sha256': digest(previous)}
    old_bindings.pop('effective_config_sha256', None)
    unresolved = set()
    for folder, terminal in [('tool-calls', {'receipt_saved'}),
                             ('requests', {'completed', 'failed', 'interrupted'})]:
        for path in (store.root / folder).glob('*.json'):
            record = store.read(f'{folder}/{path.name}')
            if record.get('state' if folder == 'tool-calls' else 'status') not in terminal:
                unresolved.add(record.get('identity', record).get('session_id'))
    for path in (store.root / 'sessions').glob('*.json'):
        session = store.read('sessions/' + path.name)
        if (session.get('active_turn_id') or session.get('active_request_id')
                or session.get('status') not in {'ready', 'recovery_required', 'closed'}
                or session.get('id') in unresolved
                or any(session.get(key) != value for key, value in old_bindings.items())):
            continue
        evidence = f'runtime-upgrades/{VERSION}/{path.name}'
        if store.read(evidence) is None:
            store.write(evidence, {'from': old.runtime_version, 'to': RUNTIME_VERSION,
                                   'previous_session': session})
        store.write('sessions/' + path.name, {**session, **bindings})
