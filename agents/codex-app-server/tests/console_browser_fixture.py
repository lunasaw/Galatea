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


class BrowserHost(test_console.FakeHost):
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

    async def send_message(self, session_id, request_id, text):
        turn = 'turn-' + request_id
        self.events.append(session_id, {'kind': 'message_accepted', 'thread_id': session_id,
                           'turn_id': turn, 'host_request_id': request_id, 'public': {'text': text}})
        def wire(method, params):
            self.events.observer('runtime_to_host', {'method': method, 'params': {'threadId': session_id, 'turnId': turn, **params}}, runtime_instance_id='browser-fixture')
        self.events.bind(session_id, session_id)
        wire('turn/started', {'turn': {'id': turn, 'status': 'inProgress'}})
        wire('item/agentMessage/delta', {'itemId': 'answer', 'delta': '你好，'})
        await asyncio.sleep(.1)
        wire('item/agentMessage/delta', {'itemId': 'answer', 'delta': '完整回复。'})
        wire('item/completed', {'item': {'id': 'answer', 'type': 'agentMessage', 'text': '你好，完整回复。'}})
        wire('item/completed', {'item': {'id': 'call', 'type': 'dynamicToolCall', 'tool': 'galatea_observe_job',
             'arguments': {'job_id': 'synthetic-job'}, 'status': 'completed', 'success': True}})
        wire('turn/completed', {'turn': {'id': turn, 'status': 'completed'}})
        return {'request_id': request_id, 'turn_id': turn, 'status': 'accepted'}


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as directory:
        events = ConsoleObservationService()
        host = BrowserHost(events, Path(directory))
        app = ConsoleApp(host, events)
        uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')
