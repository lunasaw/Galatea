"""Migrate idle source-mode sessions across the tested 0.154 -> 0.163 upgrade."""
import copy
import json
from pathlib import Path
import subprocess

from .catalog import digest
from .config import AgentConfig
from .runtime_version import VERSION, RUNTIME_VERSION
from .stage0 import file_sha256
from .state import AtomicJsonStore, identifier, payload_digest, single_writer_lock


def rebind_source_extensions(config, session_id, *, expected_previous, expected_current):
    """Explicit offline source deployment; never changes identity or grants."""
    from .local import source_manifest

    store = AtomicJsonStore(config.state_dir)
    with single_writer_lock(config.state_dir / '.host.lock'):
        return _rebind_source_extensions(config, store, session_id,
                                         measured=source_manifest(config),
                                         expected_previous=expected_previous, expected_current=expected_current)


def resume_source_extensions(config, store, session, bindings, *, measured):
    """Called under Host admission after confirming the native thread is idle."""
    unchanged = {k: v for k, v in bindings.items() if k != 'extensions_sha256'}
    if (store.read(f"sessions/{identifier(session['id'])}.json") != session
            or any(session.get(k) != v for k, v in unchanged.items())):
        raise ValueError('session or runtime bindings changed')
    return _rebind_source_extensions(config, store, session['id'],
                                     measured=measured,
                                     expected_previous=session['extensions_sha256'],
                                     expected_current=bindings['extensions_sha256'])


def _rebind_source_extensions(config, store, session_id, *, measured, expected_previous, expected_current):
    if config.raw.get('release'):
        raise ValueError('release deployments require their release migration')
    identifier(expected_previous)
    identifier(expected_current)
    if measured['extensions_sha256'] != expected_current:
        raise ValueError('reviewed extension digest does not match deployed files')
    path = f'sessions/{identifier(session_id)}.json'
    session = store.read(path)
    if not session or session.get('status') not in {'ready', 'recovery_required'} or session.get('active_turn_id') or session.get('active_request_id'):
        raise ValueError('only an idle existing session can be rebound')
    unchanged = {k: measured[k] for k in ('runtime_binary_sha256', 'runtime_compatibility_sha256', 'agent_config_sha256')}
    unchanged.update(catalog_digest=config.catalog().digest,
                     principal_digest=payload_digest([config.principal_id, sorted(config.actions)]))
    if any(session.get(k) != v for k, v in unchanged.items()):
        raise ValueError('runtime, catalog, principal or configuration changed')
    evidence = f'source-upgrades/{expected_current}/{identifier(session_id)}.json'
    previous = store.read(evidence)
    if session.get('extensions_sha256') == expected_current and previous and previous['previous_session']['extensions_sha256'] == expected_previous:
        return session
    if session.get('extensions_sha256') != expected_previous:
        raise ValueError('previous extension digest changed')
    for folder, field, terminal in (
        ('tool-calls', 'state', {'receipt_saved'}),
        ('requests', 'status', {'completed', 'failed', 'interrupted'}),
    ):
        for file in (store.root / folder).glob('*.json'):
            record = store.read(f'{folder}/{file.name}')
            if record.get('identity', record).get('session_id') == session_id and record.get(field) not in terminal:
                raise ValueError('unresolved execution must be reconciled before migration')
    if previous is None:
        store.write(evidence, {'previous_session': session, 'extensions_sha256': expected_current})
    updated = {**session, 'extensions_sha256': expected_current, 'status': 'recovery_required'}
    store.write(path, updated)
    return updated


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


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Rebind one idle source session after a reviewed extension update; stop Host first.')
    parser.add_argument('--config', required=True)
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--expected-previous', required=True)
    parser.add_argument('--expected-current', required=True)
    args = parser.parse_args()
    path = Path(args.config).resolve()
    config = AgentConfig.from_dict(json.loads(path.read_text()), path.parent)
    result = rebind_source_extensions(config, args.session_id,
                                     expected_previous=args.expected_previous, expected_current=args.expected_current)
    print(json.dumps({k: result[k] for k in ('id', 'status', 'extensions_sha256')}))
