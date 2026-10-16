"""Local synthetic fixture for browser acceptance; no model or training calls."""
import asyncio
from pathlib import Path
import sys
import tempfile
import uuid
import uvicorn
import test_console
from codex_agent.console.server import ConsoleApp
from codex_agent.console.service import ConsoleObservationService
from codex_agent.native import NativeBridge


class FixtureNative(NativeBridge):
    async def call(self, method, params, request_id):
        if method == 'model/list':
            return {'data': [{'id': 'fixture', 'model': 'fixture-model', 'displayName': 'Fixture model'},
                             {'id': 'alternate', 'model': 'fixture-alternate', 'displayName': 'Alternate model'}]}
        if method == 'config/read':
            return {'config': {'model': 'fixture-model', 'features': {'shell_tool': True}, 'web_search': 'live'}}
        if method == 'skills/list':
            return {'data': [{'skills': [{'name': 'fixture-skill', 'description': 'Synthetic skill', 'path': '/fixture/SKILL.md', 'enabled': True}]}]}
        if method == 'thread/list':
            return {'data': [{'id': 'history', 'name': 'Native history fixture', 'cwd': '/fixture'}], 'nextCursor': None}
        if method == 'command/exec':
            return {'stdout': 'fixture-command-output', 'stderr': '', 'exitCode': 0}
        if method == 'fs/readFile':
            return {'dataBase64': 'Zml4dHVyZS1maWxl'}
        if method == 'thread/resume':
            return {'session': await self.host.resume(params['threadId'])}
        if method == 'thread/name/set':
            self.host.sessions[params['threadId']]['name'] = params['name']
        if method == 'thread/settings/update':
            session = self.host.sessions[params['threadId']]
            session['settings'] = {**session.get('settings', {}), **{k: v for k, v in params.items() if k != 'threadId'}}
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

    async def changed(self):
        pass

    def _session_for_thread(self, thread_id):
        return self.sessions.get(thread_id)

    async def create_session(self, session_id=None, **kwargs):
        session = await super().create_session(session_id)
        session['thread_id'] = session['id']
        session['cwd'] = '/fixture'
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
        reply = '你好，完整回复。' if 'Markdown' not in text else '## 已完成分析\n\n这是 **Markdown** 回复。\n\n| 项目 | 状态 |\n| --- | --- |\n| 能力查询 | 可用 |\n\n```python\nprint(\"hello Galatea\")\n```\n\n<img src=x onerror=\"window.injected=1\">'
        wire('item/agentMessage/delta', {'itemId': 'answer', 'delta': reply[:3]})
        await asyncio.sleep(.8)
        wire('item/agentMessage/delta', {'itemId': 'answer', 'delta': reply[3:]})
        wire('item/completed', {'item': {'id': 'answer', 'type': 'agentMessage', 'text': reply}})
        self.events.observer('runtime_to_host', {'id': 1, 'method': 'item/tool/call', 'params': {'threadId': session_id, 'turnId': turn, 'callId': 'call', 'tool': 'galatea_observe_job', 'arguments': {'job_id': 'synthetic-job'}}}, runtime_instance_id='browser-fixture')
        self.events.observer('host_to_runtime', {'id': 1, 'result': {'success': True, 'contentItems': [{'type': 'inputText', 'text': '{"ok":true,"data":{"job_id":"synthetic-job","status":"completed"}}'}]}}, runtime_instance_id='browser-fixture')
        wire('item/completed', {'item': {'id': 'call', 'type': 'dynamicToolCall', 'tool': 'galatea_observe_job',
             'arguments': {'job_id': 'synthetic-job'}, 'status': 'completed', 'success': True,
             'contentItems': [{'type': 'inputText', 'text': '{"job_id":"synthetic-job","status":"completed"}'}]}})
        wire('turn/completed', {'turn': {'id': turn, 'status': 'completed'}})
        self.sessions[session_id].update(status='ready', active_turn_id=None)
        return {'request_id': request_id, 'turn_id': turn, 'status': 'accepted'}


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as directory:
        events = ConsoleObservationService()
        host = BrowserHost(events, Path(directory))
        app = ConsoleApp(host, events)
        uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')
