"""Local synthetic fixture for browser acceptance; no model or training calls."""
import asyncio
import base64
import copy
import os
from pathlib import Path
import sys
import tempfile
import uuid
import uvicorn
import test_console
from codex_agent.console.server import ConsoleApp
from codex_agent.console.service import ConsoleObservationService
from codex_agent.native import NativeBridge, PROTOCOL


class FixtureNative(NativeBridge):
    async def call(self, method, params, request_id):
        PROTOCOL.validate(PROTOCOL.schema(method), params)
        params = params or {}
        if method == 'account/read':
            return {'account': {'type': 'chatgpt', 'email': 'fixture@example.test', 'planType': 'pro'}, 'requiresOpenaiAuth': True}
        if method == 'account/rateLimits/read':
            return {'rateLimits': {'limitId': 'codex', 'limitName': 'Codex',
                'primary': {'usedPercent': 24, 'windowDurationMins': 300, 'resetsAt': 1900000000},
                'secondary': None, 'credits': {'unlimited': False, 'hasCredits': True, 'balance': '12.50'}}}
        if method == 'thread/compact/start': return {}
        if method == 'feedback/upload': return {'threadId': params.get('threadId')}
        if method == 'app/installed': return {'apps': [{'id': 'fixture-app', 'runtimeName': 'Fixture App', 'enabled': True, 'callable': True}]}
        if method == 'plugin/installed': return {'marketplaces': [{'name': 'fixture', 'plugins': [{'id': 'fixture-plugin@fixture', 'name': 'fixture-plugin', 'installed': True, 'enabled': True}]}], 'marketplaceLoadErrors': []}
        if method == 'thread/realtime/listVoices':
            return {'voices': {'defaultV1': 'alloy', 'defaultV2': 'marin', 'v1': ['alloy', 'ash'], 'v2': ['marin', 'cedar']}}
        if method.startswith('thread/realtime/'):
            def emit(name, fields):
                self.host.events.observer('runtime_to_host', {'method': 'thread/realtime/' + name,
                    'params': {'threadId': params['threadId'], **fields}}, runtime_instance_id='fixture')
            if method.endswith('/start'):
                emit('started', {'realtimeSessionId': params.get('realtimeSessionId'), 'version': params.get('version', 'v1')})
            elif method.endswith('/appendText') or method.endswith('/appendSpeech'):
                emit('transcript/delta', {'role': 'assistant', 'delta': '语音回复：'})
                emit('transcript/done', {'role': 'assistant', 'text': '语音回复：' + params['text']})
                emit('outputAudio/delta', {'audio': {'data': base64.b64encode(b'\0' * 4800).decode(), 'sampleRate': 24000, 'numChannels': 1, 'samplesPerChannel': 2400}})
            elif method.endswith('/stop'): emit('closed', {'reason': 'client_stop'})
            await self.host.events.flush()
            return {}
        if method == 'thread/timeline/list':
            return {'data': [{'position': 1, 'type': 'realtime', 'item': {'id': 'rt-1', 'realtimeSessionId': 'fixture-rt', 'type': 'transcriptSegment', 'role': 'user', 'text': '历史语音文字'}}], 'nextCursor': None, 'activeRealtimeSessionAtPageStart': None}
        if method == 'mcpServerStatus/list':
            return {'data': [{'name': 'fixture-app', 'authStatus': 'unsupported', 'tools': {
                'lookup': {'name': 'lookup', 'inputSchema': {'type': 'object'}, 'annotations': {'readOnlyHint': True}, '_meta': {'ui': {'resourceUri': 'ui://fixture/app'}}},
                'model_only': {'name': 'model_only', 'inputSchema': {'type': 'object'}, '_meta': {'ui': {'visibility': ['model']}}},
                'change': {'name': 'change', 'inputSchema': {'type': 'object'}, 'annotations': {'readOnlyHint': False}},
            }, 'resources': [], 'resourceTemplates': []}], 'nextCursor': None}
        if method == 'turn/start':
            return await self.host.send_message(params['threadId'], request_id, '\n'.join(p['text'] for p in params['input'] if p['type'] == 'text'), input_items=params['input'])
        if method == 'turn/steer':
            if self.host.sessions[params['threadId']].get('active_turn_id') != params['expectedTurnId']:
                raise ValueError('turn identity changed')
            return {'turnId': params['expectedTurnId']}
        if method == 'mcpServer/tool/call':
            return {'content': [{'type': 'text', 'text': 'native app tool result'}], 'isError': False}
        if method == 'mcpServer/resource/read':
            return {'contents': [{'uri': params['uri'], 'mimeType': 'text/html;profile=mcp-app',
                'text': (Path(__file__).parent / 'fixtures/mcp_app.html').read_text()}]}
        if method in {'configRequirements/read', 'modelProvider/capabilities/read'}:
            return {'requirements': None} if method == 'configRequirements/read' else {'imageGeneration': True, 'namespaceTools': True, 'webSearch': False}
        if method == 'permissionProfile/list':
            return {'data': [{'id': 'read-only', 'allowed': True, 'description': '只读'}, {'id': 'blocked', 'allowed': False}], 'nextCursor': None}
        if method == 'experimentalFeature/list':
            return {'data': [{'name': 'goals', 'displayName': '长期目标', 'stage': 'beta', 'enabled': True, 'defaultEnabled': False}], 'nextCursor': None}
        if method == 'collaborationMode/list':
            return {'data': [{'name': '计划', 'mode': 'plan', 'model': 'fixture-model', 'reasoning_effort': 'high'}, {'name': '执行', 'mode': 'default'}]}
        if method == 'thread/goal/get': return {'goal': self.host.goals.get(params['threadId'])}
        if method == 'thread/goal/clear': self.host.goals.pop(params['threadId'], None); return {}
        if method == 'thread/goal/set':
            goal = self.host.goals.setdefault(params['threadId'], {'status': 'active', 'tokensUsed': 12, 'timeUsedSeconds': 3})
            goal.update(params); return {'goal': goal}
        if method == 'thread/queue/list': return {'data': self.host.queues.get(params['threadId'], []), 'nextCursor': None}
        if method == 'thread/queue/add':
            self.host.queues.setdefault(params['threadId'], []).append({'id': request_id, 'input': params['input'], 'clientUserMessageId': params['clientUserMessageId']}); return {}
        if method == 'thread/queue/update':
            row = next(r for r in self.host.queues[params['threadId']] if r['id'] == params['queuedSubmissionId']); row['input'] = params['input']; return {}
        if method == 'thread/queue/delete':
            self.host.queues[params['threadId']] = [r for r in self.host.queues[params['threadId']] if r['id'] != params['queuedSubmissionId']]; return {}
        if method == 'thread/queue/reorder':
            rows = {r['id']: r for r in self.host.queues[params['threadId']]}; self.host.queues[params['threadId']] = [rows[i] for i in params['queuedSubmissionIds']]; return {}
        if method == 'thread/queue/start':
            row = next(r for r in self.host.queues[params['threadId']] if r['id'] == params['queuedSubmissionId'])
            self.host.queues[params['threadId']].remove(row)
            await self.host.send_message(params['threadId'], request_id, '\n'.join(p['text'] for p in row['input'] if p['type'] == 'text'))
            return {'turn': {'id': 'turn-' + request_id, 'status': 'completed'}}
        if method == 'thread/backgroundTerminals/list':
            return {'data': list(self.host.background.values()), 'nextCursor': None}
        if method == 'thread/backgroundTerminals/terminate':
            self.host.background.pop(params['processId'], None); return {}
        if method == 'thread/backgroundTerminals/clean':
            self.host.background.clear(); return {}
        if method in {'fuzzyFileSearch/sessionStart', 'fuzzyFileSearch/sessionStop'}: return {}
        if method == 'fuzzyFileSearch/sessionUpdate':
            files = [{'file_name': 'README.md', 'path': 'README.md', 'root': '/fixture', 'match_type': 'file', 'score': 100}]
            self.host.events.observer('runtime_to_host', {'method': 'fuzzyFileSearch/sessionUpdated', 'params': {**params, 'files': files}}, runtime_instance_id='fixture')
            await self.host.events.flush(); return {}
        if method == 'fs/getMetadata': return {'isFile': params['path'].endswith('.txt'), 'isDirectory': not params['path'].endswith('.txt'), 'isSymlink': False, 'modifiedAtMs': 1000, 'createdAtMs': 0}
        if method in {'fs/copy', 'fs/remove', 'fs/watch', 'fs/unwatch', 'fs/writeFile'}: return {}
        if method == 'process/spawn':
            self.host.processes[params['processHandle']] = params
            return {}
        if method == 'process/writeStdin':
            key = params['processHandle']; raw = base64.b64decode(params.get('deltaBase64', ''))
            self.host.events.observer('runtime_to_host', {'method': 'process/outputDelta', 'params': {'processHandle': key, 'stream': 'stdout', 'deltaBase64': base64.b64encode(raw).decode(), 'capReached': False}}, runtime_instance_id='fixture')
            await self.host.events.flush(); return {}
        if method == 'process/kill':
            self.host.events.observer('runtime_to_host', {'method': 'process/exited', 'params': {'processHandle': params['processHandle'], 'stdout': '', 'stderr': '', 'exitCode': 0, 'stdoutCapReached': False, 'stderrCapReached': False}}, runtime_instance_id='fixture')
            await self.host.events.flush(); return {}
        if method == 'process/resizePty': return {}
        if method == 'project/list':
            return {'data': list(self.host.projects.values()), 'nextCursor': None}
        if method in {'project/create', 'project/import'}:
            existing = next((p for p in self.host.projects.values() if p.get('key') == params['idempotencyKey']), None)
            project = existing or {'id': 'project-' + uuid.uuid4().hex, 'key': params['idempotencyKey'],
                'name': params['name'], 'roots': params['roots'], 'metadata': params.get('metadata', {})}
            self.host.projects[project['id']] = project
            for thread in params.get('threads', []):
                self.host.sessions[thread].setdefault('settings', {})['projectId'] = project['id']
            return {'project': project}
        if method in {'project/read', 'project/update'}:
            project = self.host.projects[params['projectId']]
            if method == 'project/update':
                project.update({k: v for k, v in params.items() if k != 'projectId'})
            return {'project': project}
        if method == 'project/move':
            project = self.host.projects.pop(params['projectId'])
            rows = list(self.host.projects.items())
            index = next((i for i, (key, _) in enumerate(rows) if key == params.get('beforeProjectId')), len(rows))
            rows.insert(index, (project['id'], project)); self.host.projects = dict(rows)
            return {'project': project}
        if method == 'project/delete':
            self.host.projects.pop(params['projectId'])
            for session in self.host.sessions.values():
                if session.get('settings', {}).get('projectId') == params['projectId']:
                    session['settings']['projectId'] = ''
            return {}
        if method == 'thread/metadata/update':
            self.host.sessions[params['threadId']].setdefault('settings', {})['projectId'] = params['projectId']
            return {}
        if method == 'threadSection/list':
            return {'data': list(self.host.thread_sections.values()), 'nextCursor': None}
        if method in {'threadSection/create', 'threadSection/update'}:
            section = {'id': params.get('sectionId') or uuid.uuid4().hex, 'name': params['name']}
            self.host.thread_sections[section['id']] = section
            return {'section': section}
        if method == 'threadSection/delete':
            self.host.thread_sections.pop(params['sectionId'])
            for session in self.host.sessions.values():
                if session.get('section_id') == params['sectionId']: session['section_id'] = None
            return {}
        if method == 'thread/section/move':
            self.host.sessions[params['threadId']]['section_id'] = params['sectionId']; return {}
        if method == 'model/list':
            return {'data': [{'id': 'fixture', 'model': 'fixture-model', 'displayName': 'Fixture model', 'defaultReasoningEffort': 'medium', 'supportedReasoningEfforts': [{'reasoningEffort': v} for v in ['low', 'medium', 'high', 'xhigh', 'ultra']]},
                             {'id': 'alternate', 'model': 'fixture-alternate', 'displayName': 'Alternate model', 'defaultReasoningEffort': 'high', 'supportedReasoningEfforts': [{'reasoningEffort': v} for v in ['medium', 'high']]}]}
        if method == 'config/read':
            return {'config': {'model': 'fixture-model', 'features': {'shell_tool': True}, 'web_search': 'live'}}
        if method == 'skills/list':
            return {'data': [{'skills': [{'name': 'fixture-skill', 'description': 'Synthetic skill', 'path': '/fixture/SKILL.md', 'enabled': True}]}]}
        if method == 'thread/list':
            if params.get('sortKey') == 'section_position' and not params.get('sectionId'):
                raise ValueError('section-position sorting requires a section filter')
            rows = [{**session, 'projectId': session.get('settings', {}).get('projectId'),
                     'section': self.host.thread_sections.get(session.get('section_id')),
                     'name': session.get('name', 'Native history fixture' if session['id'] == 'history' else session['id'])}
                    for session in self.host.sessions.values() if bool(session.get('archived')) == bool(params.get('archived'))
                    and ('sectionId' not in params or session.get('section_id') == params['sectionId'])]
            return {'data': [row for row in rows if params.get('searchTerm', '').lower() in row['name'].lower()], 'nextCursor': None}
        if method == 'thread/search':
            rows = []
            for thread_id, turns in self.host.turns.items():
                session = self.host.sessions[thread_id]
                if bool(session.get('archived')) != bool(params.get('archived')): continue
                text = '\n'.join(part.get('text', '') for turn in turns for item in turn['items'] for part in item.get('content', [item]))
                if params['searchTerm'] in text: rows.append({'thread': {**session, 'name': session.get('name', thread_id)}, 'snippet': text})
            return {'data': rows, 'nextCursor': None}
        if method == 'thread/searchOccurrences':
            rows = []
            for turn in self.host.turns.get(params['threadId'], []):
                for item in turn['items']:
                    text = '\n'.join(part.get('text', '') for part in item.get('content', [item]))
                    if params['searchTerm'] not in text: continue
                    start = len(text[:text.index(params['searchTerm'])].encode('utf-16-le')) // 2
                    rows.append({'itemId': item['id'], 'turnId': turn['id'], 'turnCursor': turn['id'], 'snippet': text,
                                 'snippetMatchRange': {'start': start, 'end': start + len(params['searchTerm'].encode('utf-16-le')) // 2}})
            return {'data': rows, 'nextCursor': None}
        thread_id = params.get('threadId')
        if method == 'thread/read':
            return {'thread': {'id': thread_id, 'historyMode': 'legacy', 'projectId': self.host.sessions.get(thread_id, {}).get('settings', {}).get('projectId'), 'section': self.host.thread_sections.get(self.host.sessions.get(thread_id, {}).get('section_id')), 'turns': copy.deepcopy(self.host.turns.get(thread_id, []))}}
        if method in {'thread/archive', 'thread/unarchive'}:
            self.host.sessions[thread_id]['archived'] = method == 'thread/archive'
            return {}
        if method == 'thread/delete':
            del self.host.sessions[thread_id]
            self.host.turns.pop(thread_id, None)
            return {}
        if method == 'thread/fork':
            source = self.host.sessions[thread_id]
            session = await self.host.create_session('fork-' + request_id, options=source.get('settings'))
            session['cwd'] = source['cwd']
            turns = copy.deepcopy(self.host.turns.get(thread_id, []))
            if params.get('lastTurnId'):
                turns = turns[:next(i for i, t in enumerate(turns) if t['id'] == params['lastTurnId']) + 1]
            self.host.turns[session['id']] = turns
            self.host.restore_thread(session['id'])
            return {'session': session}
        if method == 'thread/rollback':
            self.host.turns[thread_id] = self.host.turns.get(thread_id, [])[:-params['numTurns']]
            self.host.restore_thread(thread_id)
            return {'thread': {'id': thread_id, 'turns': self.host.turns[thread_id]}}
        if method == 'command/exec':
            return {'stdout': 'fixture-command-output', 'stderr': '', 'exitCode': 0}
        if method == 'fs/readDirectory':
            return {'entries': [{'fileName': 'src', 'isDirectory': True, 'isFile': False},
                                {'fileName': 'README.md', 'isDirectory': False, 'isFile': True}]}
        if method == 'fs/createDirectory':
            return {}
        if method == 'fs/readFile':
            if params['path'] == '/fixture/README.md':
                text = '# 会话界面\n\n文件可在右侧阅读，并保留输入草稿。\n\n[阅读源码](src/workbench.tsx#L84-L86)\n\n| 功能 | 状态 |\n| --- | --- |\n| 文件预览 | 可用 |\n\n```typescript\nconst desktop = true;\n```\n\n<img src=x onerror="window.injected=1">'
                return {'dataBase64': base64.b64encode(text.encode()).decode()}
            if params['path'] == '/fixture/src/workbench.tsx':
                text = '\n'.join(f'export const line{index} = "source line {index}";' for index in range(1, 151))
                return {'dataBase64': base64.b64encode(text.encode()).decode()}
            return {'dataBase64': 'Zml4dHVyZS1maWxl'}
        if method == 'thread/resume':
            return {'session': await self.host.resume(params['threadId'])}
        if method == 'thread/name/set':
            self.host.sessions[params['threadId']]['name'] = params['name']
        if method == 'thread/settings/update':
            session = self.host.sessions[params['threadId']]
            session['settings'] = {**session.get('settings', {}), **{k: v for k, v in params.items() if k != 'threadId'}}
            if params.get('cwd'):
                session['cwd'] = params['cwd']
        if method == 'turn/steer':
            assert self.host.sessions[params['threadId']]['active_turn_id'] == params['expectedTurnId']
            self.host.events.append(params['threadId'], {'kind': 'message_accepted', 'thread_id': params['threadId'],
                'turn_id': params['expectedTurnId'], 'host_request_id': request_id, 'public': {'text': params['input'][0]['text']}})
        return {}


class BrowserHost(test_console.FakeHost):
    def __init__(self, events, root):
        super().__init__(events, root)
        self.workspace_dir = root
        self.configuration_changed = self.changed
        self.native = FixtureNative(self)
        self.turns = {}
        self.projects = {}
        self.thread_sections = {}
        self.goals = {}
        self.queues = {}
        self.processes = {}
        self.background = {'background-1': {'processId': 'background-1', 'command': 'fixture watcher', 'cwd': '/fixture', 'osPid': 42, 'cpuPercent': 0.5, 'rssKb': 1024}}

    def restore_thread(self, session_id):
        self.events.append(session_id, {"kind": "thread_restored", "thread_id": session_id, "public": {"thread": {"id": session_id, "turns": self.turns[session_id]}}})

    async def changed(self):
        pass

    def _session_for_thread(self, thread_id):
        return self.sessions.get(thread_id)

    async def create_session(self, session_id=None, **kwargs):
        session = await super().create_session(session_id)
        session['thread_id'] = session['id']
        session['cwd'] = (kwargs.get('options') or {}).get('cwd', '/fixture')
        session['settings'] = kwargs.get('options') or {}
        return session
    async def interrupt(self, session_id):
        self.sessions[session_id]['status'] = 'interrupted'
        return self.sessions[session_id]

    async def close_session(self, session_id):
        self.sessions[session_id]['status'] = 'closed'
        return self.sessions[session_id]

    async def resume(self, session_id):
        self.sessions[session_id]['status'] = 'ready'
        return self.sessions[session_id]

    async def start(self):
        await self.create_session('history')
        for index in range(450):
            self.events.append('history', {'kind': 'message_accepted', 'host_request_id': f'history-{index}',
                               'public': {'text': f'history message {index}'}})
        await self.events.flush()

    async def send_message(self, session_id, request_id, text, **kwargs):
        turn = 'turn-' + request_id
        self.sessions[session_id].update(status='running', active_turn_id=turn)
        self.events.append(session_id, {'kind': 'message_accepted', 'thread_id': session_id,
                           'turn_id': turn, 'host_request_id': request_id, 'public': {'text': text, 'content': kwargs.get('input_items')}})
        if text == 'approval-fixture':
            for method, params in [
                ('item/commandExecution/requestApproval', {'command': 'printf approval-fixture', 'cwd': '/fixture', 'availableDecisions': ['accept', 'decline']}),
                ('item/tool/requestUserInput', {'questions': [{'id': 'target', 'header': 'Target', 'question': '选择执行目标', 'options': [{'label': '测试目录', 'description': 'Synthetic target'}]}]}),
                ('mcpServer/elicitation/request', {'mode': 'form', 'message': 'MCP fixture input', 'requestedSchema': {'type': 'object', 'properties': {'name': {'type': 'string'}}}}),
            ]:
                result = await self.native.request({'id': method, 'method': method, 'params': {'threadId': session_id, 'turnId': turn, **params}})
                if 'error' in result:
                    raise RuntimeError('fixture response failed')
        def wire(method, params):
            self.events.observer('runtime_to_host', {'method': method, 'params': {'threadId': session_id, 'turnId': turn, **params}}, runtime_instance_id='browser-fixture')
        self.events.bind(session_id, session_id)
        wire('turn/started', {'turn': {'id': turn, 'status': 'inProgress'}})
        wire('item/started', {'item': {'id': 'reasoning', 'type': 'reasoning', 'summary': [], 'content': []}})
        wire('item/reasoning/summaryTextDelta', {'itemId': 'reasoning', 'summaryIndex': 0, 'delta': '先检查当前可用能力。'})
        await asyncio.sleep(1.2)
        wire('item/reasoning/summaryPartAdded', {'itemId': 'reasoning', 'summaryIndex': 1})
        wire('item/reasoning/summaryTextDelta', {'itemId': 'reasoning', 'summaryIndex': 1, 'delta': '再整理结果与下一步建议。'})
        wire('item/reasoning/textDelta', {'itemId': 'reasoning', 'contentIndex': 0, 'delta': 'PRIVATE_REASONING_FIXTURE'})
        await asyncio.sleep(.8)
        wire('item/completed', {'item': {'id': 'reasoning', 'type': 'reasoning',
             'summary': ['已检查当前可用能力。', '已整理结果与下一步建议。'], 'content': ['PRIVATE_REASONING_FIXTURE']}})
        if text in ('parity-fixture', 'desktop-ui-fixture'):
            wire('item/completed', {'item': {'id': 'progress', 'type': 'agentMessage', 'phase': 'commentary', 'text': '正在检查命令与文件。'}})
            wire('turn/plan/updated', {'explanation': '验证会话展示', 'plan': [{'step': '读取配置', 'status': 'completed'}, {'step': '检查输出', 'status': 'inProgress'}]})
            wire('item/completed', {'item': {'id': 'command', 'type': 'commandExecution', 'command': 'printf parity-output', 'cwd': '/fixture', 'status': 'completed', 'exitCode': 0, 'durationMs': 123, 'aggregatedOutput': 'parity-output'}})
            wire('item/completed', {'item': {'id': 'failed', 'type': 'commandExecution', 'command': 'exit 1', 'cwd': '/fixture', 'status': 'failed', 'exitCode': 1, 'aggregatedOutput': 'parity-error'}})
            wire('item/completed', {'item': {'id': 'patch', 'type': 'fileChange', 'status': 'completed', 'changes': [{'path': '/fixture/file.py', 'kind': {'type': 'update'}, 'diff': '-old\n+new'}]}})
        if text == 'desktop-ui-fixture':
            wire('item/completed', {'item': {'id': 'read-files', 'type': 'commandExecution', 'command': 'cat README.md src/workbench.tsx', 'commandActions': [
                {'type': 'read', 'command': 'cat README.md', 'name': 'README.md', 'path': '/fixture/README.md'},
                {'type': 'read', 'command': 'cat src/workbench.tsx', 'name': 'workbench.tsx', 'path': '/fixture/src/workbench.tsx'}],
                'cwd': '/fixture', 'status': 'completed', 'exitCode': 0, 'aggregatedOutput': 'synthetic read output'}})
            wire('item/completed', {'item': {'id': 'search-files', 'type': 'commandExecution', 'command': 'rg -n preview src', 'commandActions': [
                {'type': 'search', 'command': 'rg -n preview src', 'query': 'preview', 'path': 'src'}],
                'cwd': '/fixture', 'status': 'completed', 'exitCode': 0, 'aggregatedOutput': 'src/workbench.tsx:84: preview'}})
        if text == 'mcp-app-fixture':
            wire('item/completed', {'item': {'id': 'mcp-app', 'type': 'mcpToolCall', 'server': 'fixture-app', 'tool': 'lookup',
                'status': 'completed', 'arguments': {'q': 'initial'}, 'mcpAppUi': {'resourceUri': 'ui://fixture/app'},
                'result': {'content': [{'type': 'text', 'text': 'MCP App ready'}]}}})
        reply = '你好，完整回复。' if 'Markdown' not in text else '## 已完成分析\n\n这是 **Markdown** 回复。\n\n| 项目 | 状态 |\n| --- | --- |\n| 能力查询 | 可用 |\n\n```python\nprint(\"hello Galatea\")\n```\n\n<img src=x onerror=\"window.injected=1\">'
        if text == 'parity-fixture':
            reply = '验收完成。\n\n[查看报告](/fixture/report.md) · [查看源码](/fixture/file.py:2)'
        if text == 'desktop-ui-fixture':
            reply = '会话交互已调整。点击文件后可在右侧阅读，对话与草稿会保留。\n\n[阅读说明](/fixture/README.md) · [定位源码](/fixture/src/workbench.tsx#L84-L86)\n\n[相对路径](src/workbench.tsx:12) · [中文文件](docs/%E7%95%8C%E9%9D%A2%20%E8%AF%B4%E6%98%8E.md) · [重试文件](/fixture/missing.md) · [慢速文件](/fixture/slow.txt) · [外部文档](https://developers.openai.com/codex/app/features)'
        if text == 'copy-fixture':
            reply = '## 完整复制验收\n\n' + '\n\n'.join(
                f'第 {index} 段：这段内容必须完整保留。**中文和标点**、`代码`、链接 [文档](https://example.test/docs)。'
                for index in range(160)
            ) + '\n\n```python\nprint("完整代码")\n```\n\n| 字段 | 内容 |\n| --- | --- |\n| 结果 | 全部保留 |\n\n最后一行：回答已完整复制。'
        wire('item/agentMessage/delta', {'itemId': 'answer', 'delta': reply[:3]})
        await asyncio.sleep(.8)
        wire('item/agentMessage/delta', {'itemId': 'answer', 'delta': reply[3:]})
        wire('item/completed', {'item': {'id': 'answer', 'type': 'agentMessage', 'text': reply, 'phase': 'final_answer'}})
        if text != 'desktop-ui-fixture':
            self.events.observer('runtime_to_host', {'id': 1, 'method': 'item/tool/call', 'params': {'threadId': session_id, 'turnId': turn, 'callId': 'call', 'tool': 'galatea_observe_job', 'arguments': {'job_id': 'synthetic-job'}}}, runtime_instance_id='browser-fixture')
            self.events.observer('host_to_runtime', {'id': 1, 'result': {'success': True, 'contentItems': [{'type': 'inputText', 'text': '{"ok":true,"data":{"job_id":"synthetic-job","status":"completed"}}'}]}}, runtime_instance_id='browser-fixture')
            wire('item/completed', {'item': {'id': 'call', 'type': 'dynamicToolCall', 'tool': 'galatea_observe_job',
                 'arguments': {'job_id': 'synthetic-job'}, 'status': 'completed', 'success': True,
                 'contentItems': [{'type': 'inputText', 'text': '{"job_id":"synthetic-job","status":"completed"}'}]}})
        self.turns.setdefault(session_id, []).append({'id': turn, 'status': 'completed', 'items': [
            {'id': 'u-' + turn, 'type': 'userMessage', 'content': [{'type': 'text', 'text': text}]},
            {'id': 'a-' + turn, 'type': 'agentMessage', 'text': reply, 'phase': 'final_answer'}]})
        if text == 'slash-running-fixture':
            wire('thread/tokenUsage/updated', {'tokenUsage': {
                'last': {'inputTokens': 700, 'cachedInputTokens': 0, 'outputTokens': 110, 'reasoningOutputTokens': 0, 'totalTokens': 810},
                'total': {'inputTokens': 123000, 'cachedInputTokens': 0, 'outputTokens': 456, 'reasoningOutputTokens': 0, 'totalTokens': 123456},
                'modelContextWindow': 1000,
            }})
        wire('turn/completed', {'turn': {'id': turn, 'status': 'completed'}})
        self.sessions[session_id].update(status='ready', active_turn_id=None)
        return {'request_id': request_id, 'turn_id': turn, 'status': 'accepted'}


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as directory:
        events = ConsoleObservationService()
        host = BrowserHost(events, Path(directory))
        app = ConsoleApp(host, events, static_dir=os.environ.get('CONSOLE_BUILD_DIR'))
        uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')
