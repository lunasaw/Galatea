import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from codex_agent.console.service import ConsoleObservationService
from codex_agent.console.models import EventQuery, SnapshotQuery
from codex_agent.console.projection import redact


class RedactionTests(unittest.TestCase):
    def test_config_sources_and_credential_storage_modes_are_public_but_secrets_are_not(self):
        origin = {'name': {'type': 'user', 'file': '/fixture/config.toml'}, 'version': 'v1'}
        config = {'cli_auth_credentials_store': 'file', 'mcp_oauth_credentials_store': 'keyring',
                  'developer_instructions': 'private-instruction',
                  'model_providers': {'fixture': {'api_key': 'private-key'}}}
        source = {'config': config, 'origins': {key: origin for key in config},
                  'layers': [{'name': origin['name'], 'version': 'v1', 'config': config}]}
        for envelope in (source, {'result': source}):
            value, marks = redact(envelope, method='config/read')
            result = value.get('result', value)
            self.assertEqual(result['origins'], source['origins'])
            for public_config in (result['config'], result['layers'][0]['config']):
                self.assertEqual(public_config['cli_auth_credentials_store'], 'file')
                self.assertEqual(public_config['mcp_oauth_credentials_store'], 'keyring')
            self.assertNotIn('private-instruction', json.dumps(value))
            self.assertNotIn('private-key', json.dumps(value))
            self.assertEqual(len(marks), 4)
        requirements = {'requirements': {'cliAuthCredentialsStore': 'ephemeral'}}
        self.assertEqual(redact(requirements, method='configRequirements/read')[0], requirements)
        self.assertEqual(redact({'config': {'cli_auth_credentials_store': 'private-value'}}, method='config/read')[0]['config']['cli_auth_credentials_store'], '[redacted:credential]')
        self.assertEqual(redact({'cli_auth_credentials_store': 'file'})[0]['cli_auth_credentials_store'], '[redacted:credential]')
        self.assertEqual(redact({'origins': {'api_key': 'private-key'}}, method='config/read')[0]['origins']['api_key'], '[redacted:credential]')
        nested = {'origins': {'api_key': {**origin, 'credential': 'private-nested'}}}
        public, marks = redact(nested, method='config/read')
        self.assertEqual(public['origins']['api_key']['name'], origin['name'])
        self.assertEqual(public['origins']['api_key']['credential'], '[redacted:credential]')
        self.assertEqual(marks, [{'path': '/origins/api_key/credential', 'reason': 'credential'}])

    def test_tool_schema_role_properties_are_not_message_roles(self):
        schema = {'type': 'object', 'properties': {
            'role': {'type': 'string', 'enum': ['system', 'user']},
            'content': {'type': 'string'},
        }}
        source = {'inputSchema': schema, 'custom': {'role': ['user'], 'text': 'public'},
                  'messages': [{'role': 'developer', 'content': 'hidden'},
                               {'role': 'user', 'content': 'visible'}]}
        value, marks = redact(source)
        self.assertEqual(value['inputSchema'], schema)
        self.assertEqual(value['custom']['text'], 'public')
        self.assertEqual(value['messages'][1]['content'], 'visible')
        self.assertNotIn('hidden', json.dumps(value))
        self.assertEqual(marks, [{'path': '/messages/0/content', 'reason': 'private_instructions'}])

    def test_private_collections_and_named_tokens_are_redacted(self):
        source = {'test_samples': ['sample-value'], 'testLabels': ['label-value'],
                  'raw_logs': ['log-value'], 'artifactContents': ['artifact-value'],
                  'bearer_token': 'credential-value', 'totalTokens': 99}
        value, marks = redact(source)
        for private in ('sample-value', 'label-value', 'log-value', 'artifact-value', 'credential-value'):
            self.assertNotIn(private, json.dumps(value))
        self.assertEqual(value['totalTokens'], 99)
        self.assertEqual(len(marks), 5)

    def test_nested_credentials_private_content_and_public_long_text(self):
        source = {'apiKey': 'secret-a', 'totalTokens': 25, 'text': '中' * 70000,
                  'nested': json.dumps({'access_token': 'secret-b', 'ok': list(range(1100))}),
                  'item': {'type': 'reasoning', 'content': ['private'], 'summary': ['public']},
                  'messages': [{'role': 'system', 'content': 'hidden'}],
                  'url': 'https://example.test/object?X-Amz-Signature=secret-c'}
        value, marks = redact(source)
        self.assertEqual(source['apiKey'], 'secret-a')
        self.assertEqual(value['text'], '中' * 70000)
        self.assertEqual(value['totalTokens'], 25)
        self.assertEqual(len(json.loads(value['nested'])['ok']), 1100)
        self.assertEqual(value['item']['summary'], ['public'])
        for private in ('secret-a', 'secret-b', 'secret-c', '"private"', 'hidden'):
            self.assertNotIn(private, json.dumps(value))
        self.assertEqual({m['path'] for m in marks}, {'/apiKey', '/nested/access_token', '/item/content', '/messages/0/content', '/url'})


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = ConsoleObservationService(epoch='epoch')
        await self.service.start()
        self.addAsyncCleanup(self.service.close)

    def wire(self, message, *, direction='runtime_to_host', context=None, runtime='r'):
        return self.service.observer(direction, message, context=context, runtime_instance_id=runtime)

    async def rows(self):
        await self.service.flush()
        return (await self.service.read_events(EventQuery(limit=1000)))['events']

    async def test_moderation_metadata_is_retained_without_projecting_chat_warnings(self):
        self.service.bind('s', 'thread')
        for index in range(8):
            self.wire({'method': 'turn/moderationMetadata', 'params': {
                'threadId': 'thread', 'turnId': 'turn', 'metadata': {'sequence': index}}})
        self.wire({'method': 'warning', 'params': {
            'threadId': 'thread', 'turnId': 'turn', 'message': 'Action needs attention'}})
        rows = await self.rows()
        metadata = [row for row in rows if row['method'] == 'turn/moderationMetadata']
        self.assertEqual([row['payload']['params']['metadata']['sequence'] for row in metadata], list(range(8)))
        snapshot = await self.service.snapshot(SnapshotQuery('s'))
        self.assertEqual(len(snapshot['steps']), 1)
        self.assertEqual(snapshot['steps'][0]['detail']['message'], 'Action needs attention')

    async def test_unknown_messages_and_typed_bidirectional_rpc_ids(self):
        for rpc_id, session in ((0, 'zero'), (1, 'number'), ('1', 'string')):
            self.wire({'id': rpc_id, 'method': 'future/request', 'params': {}}, direction='host_to_runtime', context={'session_id': session})
        self.wire({'id': 1, 'method': 'future/server', 'params': {}}, context={'session_id': 'server'})
        self.wire({'id': 1, 'error': {'code': -1}}, direction='host_to_runtime')
        for rpc_id in (0, '1', 1):
            self.wire({'id': rpc_id, 'result': {}})
        rows = await self.rows()
        self.assertEqual(len(rows), 8)
        self.assertEqual([r['session_id'] for r in rows[4:]], ['server', 'zero', 'string', 'number'])
        self.assertEqual(rows[4]['method'], 'future/server')
        self.assertNotIn('method', rows[4]['payload'])
        self.assertEqual(rows[5]['rpc_id'], 0)

    async def test_every_target_server_method_is_captured_without_a_business_whitelist(self):
        expected = []
        from codex_agent.native import PROTOCOL
        for name, key in (('ServerNotification', 'notifications'), ('ServerRequest', 'server')):
            schema = PROTOCOL.bundle[key]
            for index, variant in enumerate(schema['oneOf']):
                method = variant['properties']['method']['enum'][0]
                message = {'method': method, 'params': {}}
                if name == 'ServerRequest':
                    message['id'] = index
                self.wire(message)
                expected.append(method)
        rows = await self.rows()
        self.assertEqual([row['method'] for row in rows], expected)
        self.assertEqual((await self.service.status())['observation']['status'], 'ready')

    async def test_late_binding_and_fixed_history_watermark(self):
        self.wire({'method': 'item/agentMessage/delta', 'params': {'threadId': 't', 'turnId': 'turn', 'itemId': 'i', 'delta': '你好'}})
        await self.rows()
        before = (await self.service.status())['observation']['committed_seq']
        self.wire({'id': 1, 'method': 'thread/start', 'params': {}}, direction='host_to_runtime', context={'session_id': 's'})
        self.wire({'id': 1, 'result': {'thread': {'id': 't'}}})
        await self.rows()
        old = await self.service.read_events(EventQuery(session_id='s', until='epoch:' + before))
        self.assertEqual(old['events'], [])
        current = await self.service.snapshot(SnapshotQuery('s'))
        self.assertEqual(current['messages'][0]['text'], '你好')
        self.assertGreater(current['binding_version'], 0)
        self.assertIn('binding_changed', [r['kind'] for r in await self.rows()])

    async def test_delta_final_snapshot_user_and_tool_identity_deduplication(self):
        self.service.bind('s', 't')
        self.service.append('s', {'kind': 'message_accepted', 'thread_id': 't', 'host_request_id': 'req', 'public': {'text': 'go'}})
        self.wire({'id': 1, 'method': 'turn/start', 'params': {'threadId': 't'}}, direction='host_to_runtime', context={'session_id': 's', 'host_request_id': 'req'})
        self.wire({'method': 'turn/started', 'params': {'threadId': 't', 'turn': {'id': 'u', 'status': 'inProgress'}}})
        self.wire({'method': 'item/completed', 'params': {'threadId': 't', 'turnId': 'u', 'item': {'id': 'user', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'go'}]}}})
        self.wire({'id': 1, 'result': {'turn': {'id': 'u'}}})
        self.wire({'method': 'item/agentMessage/delta', 'params': {'threadId': 't', 'turnId': 'u', 'itemId': 'i', 'delta': 'hello'}})
        self.wire({'method': 'item/completed', 'params': {'threadId': 't', 'turnId': 'u', 'item': {'id': 'i', 'type': 'agentMessage', 'text': 'hello world'}}})
        self.wire({'id': 0, 'method': 'item/tool/call', 'params': {'threadId': 't', 'turnId': 'u', 'callId': 'c', 'tool': 'galatea_observe_job', 'arguments': {}}})
        self.wire({'id': 0, 'result': {'success': True, 'contentItems': [{'type': 'inputText', 'text': '{"ok":true,"data":{"job_id":"j","status":"RUNNING"}}'}]}}, direction='host_to_runtime')
        self.wire({'method': 'item/completed', 'params': {'threadId': 't', 'turnId': 'u', 'item': {'id': 'c', 'type': 'dynamicToolCall', 'tool': 'galatea_observe_job', 'status': 'completed', 'success': True}}})
        for total in (10, 20):
            self.wire({'method': 'thread/tokenUsage/updated', 'params': {'threadId': 't', 'turnId': 'u', 'tokenUsage': {'total': {'totalTokens': total}}}})
        self.wire({'method': 'turn/completed', 'params': {'threadId': 't', 'turn': {'id': 'u', 'status': 'failed'}}})
        await self.rows()
        snapshot = await self.service.snapshot(SnapshotQuery('s'))
        self.assertEqual([r['text'] for r in snapshot['messages']], ['go', 'hello world'])
        tools = [s for s in snapshot['steps'] if s.get('type') == 'tool']
        self.assertEqual(len(tools), 1)
        self.assertIn('RUNNING', json.dumps(tools[0]['result']))
        self.assertNotIn('job', tools[0])
        self.assertTrue(tools[0]['observed_at'])
        self.assertEqual(snapshot['turns'][0]['status'], 'failed')
        self.assertEqual(snapshot['turns'][0]['usage']['total']['totalTokens'], 20)

    async def test_private_reasoning_and_capture_copy_do_not_mutate_dispatch_payload(self):
        message = {'method': 'item/reasoning/textDelta', 'params': {'delta': 'private thought'}}
        self.wire(message)
        message['params']['delta'] = 'mutated later'
        rows = await self.rows()
        self.assertNotIn('private thought', json.dumps(rows))
        self.assertTrue(rows[0]['redactions'])
        self.assertEqual(message['params']['delta'], 'mutated later')

    async def test_public_reasoning_summary_stream_final_identity_and_watermark(self):
        self.service.bind('s', 't')
        def wire(method, params):
            self.wire({'method': method, 'params': {'threadId': 't', 'turnId': 'u', **params}})
        wire('item/started', {'item': {'id': 'r', 'type': 'reasoning', 'summary': [], 'content': []}})
        for index, delta in ((0, 'First'), (1, 'Second'), (0, ' paragraph')):
            wire('item/reasoning/summaryTextDelta', {'itemId': 'r', 'summaryIndex': index, 'delta': delta})
        wire('item/reasoning/textDelta', {'itemId': 'r', 'contentIndex': 0, 'delta': 'hidden thought'})
        await self.rows()
        live = await self.service.snapshot(SnapshotQuery('s'))
        self.assertEqual(len(live['steps']), 1)
        reasoning = live['steps'][0]
        self.assertEqual(reasoning['summary_0'], 'First paragraph')
        self.assertEqual(reasoning['summary_1'], 'Second')
        self.assertEqual(reasoning['status'], 'inProgress')
        self.assertTrue(reasoning['started_at'])
        self.assertNotIn('hidden thought', json.dumps(live))
        wire('item/completed', {'item': {'id': 'r', 'type': 'reasoning',
             'summary': ['Final paragraph', 'Second'], 'content': ['hidden final']}})
        await self.rows()
        final = await self.service.snapshot(SnapshotQuery('s'))
        self.assertEqual(len(final['steps']), 1)
        self.assertEqual(final['steps'][0]['id'], reasoning['id'])
        self.assertEqual(final['steps'][0]['detail']['summary'], ['Final paragraph', 'Second'])
        self.assertEqual(final['steps'][0]['status'], 'completed')
        self.assertTrue(final['steps'][0]['completed_at'])
        self.assertNotIn('hidden final', json.dumps(final))
        replay = await self.service.snapshot(SnapshotQuery('s', until=live['watermark']))
        self.assertEqual(replay['steps'], live['steps'])

    async def test_reasoning_delta_without_start_and_completed_only_summary(self):
        self.service.bind('s', 't')
        self.wire({'method': 'item/reasoning/summaryTextDelta', 'params': {
            'threadId': 't', 'turnId': 'u', 'itemId': 'r', 'summaryIndex': 0, 'delta': 'Public'}})
        self.wire({'method': 'item/completed', 'params': {
            'threadId': 't', 'turnId': 'v', 'item': {'id': 'r', 'type': 'reasoning', 'summary': ['Final only']}}})
        await self.rows()
        rows = (await self.service.snapshot(SnapshotQuery('s')))['steps']
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['summary_0'], 'Public')
        self.assertNotIn('started_at', rows[0])
        self.assertEqual(rows[1]['detail']['summary'], ['Final only'])

    async def test_native_item_identity_is_scoped_to_thread_and_turn(self):
        self.service.bind('s', 't')
        for turn in ('first', 'second'):
            self.wire({'method': 'item/completed', 'params': {'threadId': 't', 'turnId': turn,
                       'item': {'id': 'same', 'type': 'futureItem', 'text': turn}}})
        await self.rows()
        snapshot = await self.service.snapshot(SnapshotQuery('s'))
        self.assertEqual([step['turn_id'] for step in snapshot['steps']], ['first', 'second'])

    async def test_native_progress_patch_and_diff_keep_item_identity_and_final_results(self):
        self.service.bind('s', 't')
        def wire(method, params):
            self.wire({'method': method, 'params': {'threadId': 't', 'turnId': 'u', **params}})
        wire('item/started', {'item': {'id': 'mcp', 'type': 'mcpToolCall', 'server': 'fixture',
             'tool': 'read', 'arguments': {}, 'status': 'inProgress'}})
        wire('item/mcpToolCall/progress', {'itemId': 'mcp', 'message': 'Reading first page'})
        wire('item/mcpToolCall/progress', {'itemId': 'mcp', 'message': 'Reading second page'})
        changes = [{'path': '/fixture/a.txt', 'kind': {'type': 'update'}, 'diff': '+updated'}]
        wire('item/fileChange/patchUpdated', {'itemId': 'patch', 'changes': changes})
        wire('turn/diff/updated', {'diff': '+updated'})
        await self.rows()
        live = (await self.service.snapshot(SnapshotQuery('s')))['steps']
        mcp = next(s for s in live if s['type'] == 'mcpToolCall')
        self.assertEqual(mcp['detail']['server'], 'fixture')
        self.assertEqual(mcp['text'], 'Reading first page\nReading second page\n')
        self.assertEqual(next(s for s in live if s['type'] == 'fileChange')['detail']['changes'], changes)
        self.assertEqual(next(s for s in live if s['type'] == 'turnDiff')['detail']['diff'], '+updated')
        wire('item/completed', {'item': {'id': 'mcp', 'type': 'mcpToolCall', 'status': 'completed',
             'server': 'fixture', 'tool': 'read', 'result': {'content': [{'type': 'text', 'text': 'Done'}]}}})
        await self.rows()
        final = (await self.service.snapshot(SnapshotQuery('s')))['steps']
        tools = [s for s in final if s['type'] == 'mcpToolCall']
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]['id'], mcp['id'])
        self.assertEqual(tools[0]['status'], 'completed')
        self.assertEqual(tools[0]['detail']['result']['content'][0]['text'], 'Done')
        self.assertEqual(tools[0]['text'], mcp['text'])

    async def test_store_failure_keeps_batch_and_later_messages_pending_then_retries_same_batch(self):
        original = self.service.store.commit
        attempts = []
        async def fail_after_commit(batch):
            attempts.append(batch.batch_id)
            await original(batch)
            raise OSError('synthetic lost acknowledgement')
        with patch.object(self.service.store, 'commit', fail_after_commit):
            self.wire({'method': 'one'})
            await asyncio.sleep(.03)
            self.wire({'method': 'two'})
            status = await self.service.status()
            self.assertEqual(status['observation']['status'], 'unavailable')
            self.assertGreaterEqual(status['observation']['pending_events'], 1)
        await self.service.retry()
        rows = await self.rows()
        self.assertEqual([r['method'] for r in rows], ['one', 'two'])
        self.assertEqual(len({r['event_id'] for r in rows}), 2)

    async def test_projection_failure_retains_public_wire_body_and_reports_incomplete(self):
        with patch.object(self.service.projector, 'changes', side_effect=ValueError('fixture')):
            self.wire({'method': 'future/message', 'params': {'text': 'public', 'apiKey': 'private'}})
            rows = await self.rows()
        self.assertEqual(rows[0]['payload']['params']['text'], 'public')
        self.assertNotIn('private', json.dumps(rows))
        self.assertEqual((await self.service.status())['observation']['status'], 'incomplete')

    async def test_correlation_failure_is_visible_and_does_not_kill_writer(self):
        with patch.object(self.service.correlator, 'process', side_effect=ValueError('fixture')):
            self.wire({'method': 'future/message', 'params': {'text': 'public'}})
            await self.service.flush()
        self.wire({'method': 'later/message'})
        rows = await self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['payload']['params']['text'], 'public')
        self.assertEqual((await self.service.status())['observation']['status'], 'incomplete')

    async def test_capture_boundary_failure_cannot_report_complete(self):
        self.service.diagnostic('observation_error', runtime_instance_id='r', error='fixture')
        await self.service.flush()
        self.assertEqual((await self.service.status())['observation']['status'], 'incomplete')

    async def test_transient_storage_failure_retries_automatically_with_same_batch(self):
        original = self.service.store.commit
        attempts = []
        async def transient(batch):
            attempts.append(batch.batch_id)
            if len(attempts) == 1:
                raise OSError('temporary outage')
            return await original(batch)
        with patch.object(self.service.store, 'commit', transient):
            self.wire({'method': 'recoverable'})
            async with asyncio.timeout(.5):
                while len(attempts) < 2:
                    await asyncio.sleep(.01)
            await self.service.flush()
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual((await self.service.status())['observation']['status'], 'ready')


if __name__ == '__main__':
    unittest.main()
