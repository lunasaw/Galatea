"""Copy a quiescent memory Console's public event history into a new SQLite DB."""
import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from codex_agent.console.adapters.sqlite import SqliteConsoleObservationStore
from codex_agent.console.models import ObservationBatch, SnapshotQuery
from codex_agent.console.projection import Projector


async def migrate(url, output):
    def get(path):
        with urlopen(url.rstrip('/') + path, timeout=30) as response:
            return json.load(response)
    sessions = get('/api/sessions')
    if any(s.get('active_turn_id') or s['status'] in {'starting', 'running', 'interrupting', 'closing'} for s in sessions):
        raise ValueError('finish active turns before migrating history')
    if output.exists():
        raise ValueError('destination already exists; refusing to overwrite')
    page = get('/api/events/history?limit=1000')
    epoch, watermark = page['epoch'], page['watermark']
    rows = list(page['events'])
    while page['has_more']:
        page = get('/api/events/history?' + urlencode({'limit': 1000, 'after': page['next_cursor'], 'until': watermark}))
        rows.extend(page['events'])
    store = SqliteConsoleObservationStore(output)
    await store.open(epoch)
    projector, bindings = Projector(), {}
    try:
        for row in rows:
            changes = projector.changes(row)
            if row.get('thread_id') and row.get('session_id') and bindings.get(row['thread_id']) != row['session_id']:
                bindings[row['thread_id']] = row['session_id']
                changes.append({'type': 'binding', 'thread_id': row['thread_id'], 'session_id': row['session_id'], 'seq': row['seq']})
            await store.commit(ObservationBatch(row['event_id'], epoch, (row,), tuple(changes)))
        counts = []
        for session in sessions:
            snapshot = await store.snapshot(SnapshotQuery(session['id'], limit=1000))
            counts.append(len(snapshot['messages']))
        print(json.dumps({'events': len(rows), 'sessions': len(sessions), 'messages': sum(counts), 'watermark': watermark}))
    finally:
        await store.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:18880')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(migrate(args.url, args.output))
