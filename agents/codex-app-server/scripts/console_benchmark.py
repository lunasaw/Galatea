"""Synthetic observation capacity measurement; no model, platform or training calls."""
import asyncio
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from codex_agent.console.models import EventQuery, SnapshotQuery
from codex_agent.console.service import ConsoleObservationService
from codex_agent.rpc import JsonRpcPeer


def rss_kib():
    # ru_maxrss may include the launching process's pre-exec high-water mark.
    line = next(line for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('VmRSS:'))
    return int(line.split()[1])


async def measure(count=8300):
    service = ConsoleObservationService(epoch='benchmark')
    await service.start()
    reader = asyncio.StreamReader()

    class EchoWriter:
        def write(self, data):
            message = json.loads(data)
            reader.feed_data(json.dumps({'id': message['id'], 'result': {'ok': True}}).encode() + b'\n')

        async def drain(self):
            await asyncio.sleep(0)

    peer = JsonRpcPeer(reader, EchoWriter(), observer=service.observer,
                       diagnostic=service.diagnostic, runtime_instance_id='synthetic')
    rss_before = rss_kib()
    start = time.perf_counter()
    service.bind('session', 'thread')
    for index in range(count):
        service.observer('runtime_to_host', {'method': 'fixture/event',
                         'params': {'threadId': 'thread', 'index': index, 'text': 'x' * 256}})
    captured_seconds = time.perf_counter() - start
    pending_at_burst = service.observer.queue.qsize()
    rpc_ms = []
    for _ in range(100):
        before = time.perf_counter()
        await peer.request('fixture/echo')
        rpc_ms.append((time.perf_counter() - before) * 1000)
    for index in range(1200):
        service.observer('runtime_to_host', {'method': 'item/completed', 'params': {
            'threadId': 'thread', 'turnId': 'turn', 'item': {'id': str(index), 'type': 'fixtureStep'}}})
    for index in range(450):
        service.append('session', {'kind': 'message_accepted', 'host_request_id': str(index),
                                  'public': {'text': 'message ' + str(index)}})
    long_body = {'text': '中' * 70000, 'list': list(range(1100))}
    service.append('session', {'kind': 'fixture', 'public': long_body})
    await service.flush()
    committed_seconds = time.perf_counter() - start
    status = await service.status()
    latencies = {}
    for label, query in [('history', lambda: service.read_events(EventQuery(session_id='session', limit=200))),
                         ('snapshot', lambda: service.snapshot(SnapshotQuery('session')))]:
        times = []
        for _ in range(20):
            before = time.perf_counter()
            await query()
            times.append((time.perf_counter() - before) * 1000)
        latencies[label + '_ms'] = {'p50': statistics.median(times), 'p95': sorted(times)[18]}
    total, cursor = 0, None
    while True:
        page = await service.read_events(EventQuery(after=cursor, limit=1000))
        total += len(page['events'])
        cursor = page['next_cursor']
        if not page['has_more']:
            break
    assert total == status['observation']['event_count']
    final = await service.detail(cursor)
    assert final['payload']['public'] == long_body
    assert len((await service.snapshot(SnapshotQuery('session', view='steps', limit=1000)))['steps']) == 1000
    result = {'fixture': 'synthetic-only; no browser subscribers', 'burst_events': count,
              'capture_events_per_second': count / captured_seconds,
              'commit_events_per_second': total / committed_seconds,
              'pending_at_burst': pending_at_burst, 'pending_after_flush': status['observation']['pending_events'],
              'retained_events': total, 'observation_status': status['observation']['status'],
              'encoded_event_bytes': status['observation']['encoded_bytes'],
              'rpc_ms': {'p50': statistics.median(rpc_ms), 'p95': sorted(rpc_ms)[94], 'max': max(rpc_ms)},
              **latencies, 'rss_before_kib': rss_before,
              'rss_after_kib': rss_kib()}
    await peer.close()
    await service.close()
    return result


if __name__ == '__main__':
    print(json.dumps(asyncio.run(measure()), ensure_ascii=False, indent=2))
