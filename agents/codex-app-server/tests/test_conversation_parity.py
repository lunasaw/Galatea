"""Native conversation projection: phase, steering, plans and durable replay."""
import json
from pathlib import Path
import tempfile
import unittest

from codex_agent.console.adapters.sqlite import SqliteConsoleObservationStore
from codex_agent.console.models import SnapshotQuery
from codex_agent.console.service import ConsoleObservationService


class ConversationParityTests(unittest.IsolatedAsyncioTestCase):
    async def test_steering_keeps_each_native_input_and_replay_preserves_phase_and_results(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'console.sqlite3'
            service = ConsoleObservationService(SqliteConsoleObservationStore(path), epoch='parity')
            await service.start()
            service.bind('s', 't')
            service.append('s', {'kind': 'message_accepted', 'thread_id': 't', 'host_request_id': 'request', 'public': {'text': 'initial'}})
            def wire(method, params, **kwargs):
                service.observer('runtime_to_host', {'method': method, 'params': {'threadId': 't', 'turnId': 'u', **params}}, runtime_instance_id='r', **kwargs)
            service.observer('host_to_runtime', {'id': 1, 'method': 'turn/start', 'params': {'threadId': 't'}}, context={'session_id': 's', 'host_request_id': 'request'}, runtime_instance_id='r')
            wire('item/completed', {'item': {'id': 'input', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'initial'}]}})
            service.observer('runtime_to_host', {'id': 1, 'result': {'turn': {'id': 'u'}}}, runtime_instance_id='r')
            for item_id in ('steer-1', 'steer-2'):
                # Both lifecycle events reference the same item. Text alone is
                # deliberately identical: only native identity may deduplicate.
                for method in ('item/started', 'item/completed'):
                    wire(method, {'item': {'id': item_id, 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'same text'}]}})
            wire('item/completed', {'item': {'id': 'progress', 'type': 'agentMessage', 'text': 'checking', 'phase': 'commentary'}})
            wire('item/plan/delta', {'itemId': 'plan', 'delta': 'partial'})
            wire('item/completed', {'item': {'id': 'plan', 'type': 'plan', 'text': 'authoritative plan'}})
            wire('item/completed', {'item': {'id': 'tool', 'type': 'dynamicToolCall', 'tool': 'echo', 'arguments': {}, 'status': 'completed', 'success': True, 'contentItems': [{'type': 'inputText', 'text': 'receipt'}]}})
            wire('item/completed', {'item': {'id': 'final', 'type': 'agentMessage', 'text': 'done', 'phase': 'final_answer'}})
            await service.flush()
            before = await service.snapshot(SnapshotQuery('s'))
            self.assertEqual([m['text'] for m in before['messages']], ['initial', 'same text', 'same text', 'checking', 'done'])
            self.assertEqual([m.get('phase') for m in before['messages'][-2:]], ['commentary', 'final_answer'])
            self.assertEqual(len([s for s in before['steps'] if s['type'] == 'plan']), 1)
            tool = next(s for s in before['steps'] if s['type'] == 'tool')
            self.assertIn('receipt', json.dumps(tool['result']))
            await service.close()
            restored = ConsoleObservationService(SqliteConsoleObservationStore(path))
            await restored.start()
            self.addAsyncCleanup(restored.close)
            after = await restored.snapshot(SnapshotQuery('s'))
            for view in ('messages', 'steps', 'turns'):
                self.assertEqual(after[view], before[view])

    async def test_late_start_response_does_not_remove_steered_inputs(self):
        service = ConsoleObservationService(epoch='parity')
        await service.start()
        self.addAsyncCleanup(service.close)
        service.bind('s', 't')
        service.append('s', {'kind': 'message_accepted', 'thread_id': 't', 'host_request_id': 'request', 'public': {'text': 'initial'}})
        service.observer('host_to_runtime', {'id': 1, 'method': 'turn/start', 'params': {'threadId': 't'}}, context={'session_id': 's', 'host_request_id': 'request'}, runtime_instance_id='r')
        for i in range(3):
            service.observer('runtime_to_host', {'method': 'item/completed', 'params': {'threadId': 't', 'turnId': 'u', 'item': {'id': f'i{i}', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'initial' if i == 0 else f'steer-{i}'}]}}}, runtime_instance_id='r')
        service.observer('runtime_to_host', {'id': 1, 'result': {'turn': {'id': 'u'}}}, runtime_instance_id='r')
        await service.flush()
        snapshot = await service.snapshot(SnapshotQuery('s'))
        self.assertEqual([m['text'] for m in snapshot['messages']], ['initial', 'steer-1', 'steer-2'])

    async def test_native_restore_keeps_turn_timing_without_inventing_reasoning_timing(self):
        service = ConsoleObservationService(epoch='parity')
        await service.start()
        self.addAsyncCleanup(service.close)
        service.append('s', {'kind': 'thread_restored', 'thread_id': 't', 'public': {'thread': {'turns': [
            {'id': 'u', 'status': 'completed', 'startedAt': 100, 'completedAt': 125, 'items': [
                {'id': 'r', 'type': 'reasoning', 'summary': ['public']},
                {'id': 'f', 'type': 'agentMessage', 'phase': 'final_answer', 'text': 'done'},
            ]}
        ]}}})
        await service.flush()
        snapshot = await service.snapshot(SnapshotQuery('s'))
        self.assertEqual(snapshot['turns'][0]['detail']['startedAt'], 100)
        self.assertNotIn('completed_at', snapshot['steps'][0])
        self.assertEqual(snapshot['messages'][0]['phase'], 'final_answer')

    async def test_old_persisted_projection_is_rebuilt_without_editing_audit_batches(self):
        from codex_agent.console.models import ObservationBatch
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'console.sqlite3'
            store = SqliteConsoleObservationStore(path)
            await store.open('legacy')
            rows = tuple({
                'schema_version': 'codex-agent.console.event/v1', 'epoch': 'legacy', 'seq': str(i),
                'event_id': f'legacy:{i}', 'session_id': 's', 'thread_id': 't', 'turn_id': 'u',
                'item_id': f'i{i}', 'host_request_id': 'same-request', 'kind': 'notification',
                'method': 'item/completed', 'observed_at': '2026-10-16T00:00:00Z',
                'payload': {'params': {'item': {'id': f'i{i}', 'type': 'userMessage',
                             'content': [{'type': 'text', 'text': text}]}}},
            } for i, text in enumerate(('initial', 'steered'), 1))
            changes = tuple({'seq': row['seq'], 'session_id': 's', 'thread_id': 't', 'view': 'messages',
                             'key': 'request:same-request', 'set': {'role': 'user', 'text': row['payload']['params']['item']['content'][0]['text']}}
                            for row in rows)
            await store.commit(ObservationBatch('legacy', 'legacy', rows, changes))
            original = store.db.execute('SELECT payload FROM batches').fetchall()
            self.assertEqual(len((await store.snapshot(SnapshotQuery('s')))['messages']), 1)
            await store.close()
            rebuilt = SqliteConsoleObservationStore(path)
            await rebuilt.open('new-process')
            try:
                self.assertEqual([m['text'] for m in (await rebuilt.snapshot(SnapshotQuery('s')))['messages']], ['initial', 'steered'])
                self.assertEqual(rebuilt.db.execute('SELECT payload FROM batches').fetchall(), original)
            finally:
                await rebuilt.close()
