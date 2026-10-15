"""Observation adapter contract, independent of the native Runtime version."""
import asyncio
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from codex_agent.console.adapters.memory import MemoryConsoleObservationStore
from codex_agent.console.models import EventQuery, ObservationBatch, SnapshotQuery
from codex_agent.console.ports import (
    CommitConflict, CursorAhead, EpochMismatch, InvalidCursor,
    PageContextMismatch, SequenceGap, StoreUnavailable,
)


def event(seq, *, session='s', method='unknown/method', text='正文'):
    return {'event_id': f'epoch:{seq}', 'epoch': 'epoch', 'seq': str(seq),
            'session_id': session, 'method': method, 'payload': {'text': text}}


class ObservationStoreContract(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = MemoryConsoleObservationStore()
        await self.store.open('epoch')
        self.addAsyncCleanup(self.store.close)

    async def put(self, *events, batch_id='batch', changes=()):
        return await self.store.commit(ObservationBatch(batch_id, 'epoch', events, changes))

    async def test_complete_retention_and_full_length_fields(self):
        rows = tuple(event(n) for n in range(1, 8301))
        rows[-1]['payload'] = {'text': '中' * 70000, 'list': list(range(1100))}
        await self.put(*rows)
        found, after, until = [], None, None
        while True:
            page = await self.store.read_events(EventQuery(after=after, until=until, limit=200))
            found.extend(page['events'])
            after, until = page['next_cursor'], page['watermark']
            if not page['has_more']:
                break
        self.assertEqual(found, list(rows))
        stats = await self.store.stats()
        self.assertEqual(stats['event_count'], 8300)
        self.assertGreater(stats['encoded_bytes'], 210000)

    async def test_commit_retry_conflict_and_atomic_sequence_validation(self):
        batch = ObservationBatch('one', 'epoch', (event(1),))
        first = await self.store.commit(batch)
        self.assertEqual(await self.store.commit(batch), first)
        changed = copy.deepcopy(batch)
        changed.events[0]['payload']['text'] = 'different'
        with self.assertRaises(CommitConflict):
            await self.store.commit(changed)
        with self.assertRaises(SequenceGap):
            await self.put(event(2), event(4), batch_id='gap')
        self.assertEqual((await self.store.stats())['event_count'], 1)
        await self.put(event(2), batch_id='two')

    async def test_committed_values_and_query_results_are_isolated(self):
        original = event(1)
        await self.put(original)
        original['payload']['text'] = 'mutated'
        page = await self.store.read_events(EventQuery())
        page['events'][0]['payload']['text'] = 'also mutated'
        self.assertEqual((await self.store.read_events(EventQuery()))['events'][0]['payload']['text'], '正文')

    async def test_filtered_empty_page_advances_and_fixed_watermark_excludes_new_events(self):
        await self.put(event(1), event(2, session='other'), event(3))
        page = await self.store.read_events(EventQuery(session_id='s', limit=1))
        self.assertEqual(page['watermark'], 'epoch:3')
        await self.put(event(4), batch_id='later')
        second = await self.store.read_events(EventQuery(session_id='s', after=page['next_cursor'],
                                                        until=page['watermark']))
        self.assertEqual([row['seq'] for row in second['events']], ['3'])
        empty = await self.store.read_events(EventQuery(filters={'method': 'missing'}))
        self.assertEqual(empty['events'], [])
        self.assertEqual(empty['next_cursor'], 'epoch:4')

    async def test_cursor_validation_and_new_process_epoch(self):
        await self.put(event(1))
        for cursor in ('1', 'epoch:-1', 'epoch:1.5', 'epoch:01', 'epoch:'):
            with self.subTest(cursor=cursor), self.assertRaises(InvalidCursor):
                await self.store.read_events(EventQuery(after=cursor))
        with self.assertRaises(EpochMismatch):
            await self.store.read_events(EventQuery(after='old:1'))
        with self.assertRaises(CursorAhead):
            await self.store.read_events(EventQuery(after='epoch:2'))
        replacement = MemoryConsoleObservationStore()
        await replacement.open('new')
        self.addAsyncCleanup(replacement.close)
        self.assertEqual((await replacement.stats())['event_count'], 0)
        with self.assertRaises(EpochMismatch):
            await replacement.read_events(EventQuery(after='epoch:0'))

    async def test_wait_checks_watermark_before_registering_and_close_wakes_waiters(self):
        await self.put(event(1))
        self.assertEqual(await asyncio.wait_for(self.store.wait_for_change('epoch:0', 10), .2), 'epoch:1')
        waiter = asyncio.create_task(self.store.wait_for_change('epoch:1', 10))
        await asyncio.sleep(0)
        await self.put(event(2), batch_id='two')
        self.assertEqual(await asyncio.wait_for(waiter, .2), 'epoch:2')
        self.assertEqual(await self.store.wait_for_change('epoch:2', .001), 'epoch:2')
        waiter = asyncio.create_task(self.store.wait_for_change('epoch:2', 10))
        await asyncio.sleep(0)
        await self.store.close()
        with self.assertRaises(StoreUnavailable):
            await waiter

    async def test_snapshot_changes_share_commit_watermark_and_stable_pagination(self):
        changes = tuple({'seq': str(n), 'session_id': 's', 'view': 'messages', 'key': f'm{n}',
                         'set': {'text': f'message {n}'}} for n in range(1, 4))
        await self.put(*(event(n) for n in range(1, 4)), changes=changes)
        page = await self.store.snapshot(SnapshotQuery('s', view='messages', limit=2))
        self.assertEqual([row['text'] for row in page['messages']], ['message 2', 'message 3'])
        self.assertEqual(page['watermark'], 'epoch:3')
        self.assertIsNotNone(page['next_page'])
        await self.put(event(4), batch_id='later', changes=({'seq': '4', 'session_id': 's',
                      'view': 'messages', 'key': 'm1', 'set': {'text': 'updated'}},))
        older = await self.store.snapshot(SnapshotQuery('s', view='messages', limit=2, page=page['next_page']))
        self.assertEqual(older['messages'][0]['text'], 'message 1')
        self.assertEqual(older['watermark'], 'epoch:3')
        with self.assertRaises(PageContextMismatch):
            await self.store.snapshot(SnapshotQuery('other', view='messages', limit=2, page=page['next_page']))
        with self.assertRaises(PageContextMismatch):
            await self.store.snapshot(SnapshotQuery('s', view='steps', limit=2, page=page['next_page']))

    async def test_invalid_projection_batch_does_not_publish_events(self):
        with self.assertRaises(ValueError):
            await self.put(event(1), changes=({'seq': '2', 'session_id': 's', 'view': 'messages',
                                            'key': 'm', 'set': {'text': 'future'}},))
        self.assertEqual((await self.store.stats())['event_count'], 0)


if __name__ == '__main__':
    unittest.main()
