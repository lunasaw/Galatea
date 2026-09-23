import asyncio
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from codex_agent.console.server import ConsoleApp
from codex_agent.events import EventLog
import test_console


class HTTPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_same_loop_authenticated_http_and_live_sse(self):
        import httpx
        import uvicorn
        with tempfile.TemporaryDirectory() as directory:
            events = EventLog(Path(directory))
            host = test_console.FakeHost(events)
            loops = []
            async def start():
                loops.append(asyncio.get_running_loop())
            async def stop():
                loops.append(asyncio.get_running_loop())
            host.start, host.stop = start, stop
            app = ConsoleApp(host, events, token='test-token-1234567890', allowed_origins={'http://localhost'})
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, lifespan='on', log_level='error', ws='none'))
            task = asyncio.create_task(server.serve(sockets=[sock]))
            try:
                async with asyncio.timeout(5):
                    while not server.started:
                        await asyncio.sleep(.01)
                async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=3) as client:
                    self.assertEqual((await client.get('/')).status_code, 200)
                    self.assertIn('Bearer', (await client.get('/app.js')).text)
                    self.assertEqual((await client.get('/api/sessions/s')).status_code, 401)
                    headers = {'Authorization':'Bearer test-token-1234567890','Origin':'http://localhost'}
                    self.assertEqual((await client.get('/api/sessions/s/loop/turn-1', headers=headers)).json()['steps'], [])
                    first = await client.post('/api/sessions/s/messages', headers=headers, json={'request_id':'r','text':'hello'})
                    repeated = await client.post('/api/sessions/s/messages', headers=headers, json={'request_id':'r','text':'hello'})
                    self.assertEqual(first.json(), repeated.json())
                    self.assertEqual(len(host.messages), 1)
                    async with client.stream('GET', '/api/sessions/s/events', headers=headers) as response:
                        events.append('s', {'kind':'turn','public':{'status':'completed'}})
                        lines = response.aiter_lines()
                        self.assertEqual(await anext(lines), 'id: 1')
                        self.assertEqual(json.loads((await anext(lines))[6:])['public']['status'], 'completed')
            finally:
                server.should_exit = True
                await asyncio.wait_for(task, 5)
                sock.close()
            self.assertEqual(loops, [asyncio.get_running_loop(), asyncio.get_running_loop()])
