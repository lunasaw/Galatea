"""Isolated turn-diff fixture; never modifies the user's workspace."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import uvicorn
from console_browser_fixture import BrowserHost
from codex_agent.console.server import ConsoleApp
from codex_agent.console.service import ConsoleObservationService

DIFF = '''diff --git a/frontend/src/composer-commands.tsx b/frontend/src/composer-commands.tsx
index 1111111111111111111111111111111111111111..2222222222222222222222222222222222222222
--- a/frontend/src/composer-commands.tsx
+++ b/frontend/src/composer-commands.tsx
@@ -23,3 +23,4 @@
   const [focused, setFocused] = useState(false);
+  const [invoked, setInvoked] = useState(false);
   const [dismissed, setDismissed] = useState(false);

@@ -35,3 +36,3 @@
   const session = state.sessions.find((row) => row.id === state.active);
-  const matches = disabled ? [] : filterSlashCommands(text);
+  const matches = disabled ? [] : filterSlashCommands(invoked ? "/" : text);
   const open = !dismissed && focused && matches.length > 0;
@@ -44,1 +45,2 @@
-  useEffect(() => { setDismissed(false); setSubmenu(""); setSelected(0); }, [text]);
+  useEffect(() => { setDismissed(false); setSubmenu(""); setSelected(0); setInvoked(false); }, [text]);
+  const close = () => setInvoked(false);
diff --git a/frontend/src/界面 样式.css b/frontend/src/界面 样式.css
index 1111111111111111111111111111111111111111..2222222222222222222222222222222222222222
--- a/frontend/src/界面 样式.css
+++ b/frontend/src/界面 样式.css
@@ -10,1 +10,2 @@
-.composer { padding: 8px; }
+.composer { padding: 10px; }
+.composer:focus-within { outline: 2px solid var(--ring); }
'''

class ChangesHost(BrowserHost):
    async def send_message(self, session_id, request_id, text, **kwargs):
        if text not in {'changes-fixture', 'changes-failed-fixture'}:
            return await super().send_message(session_id, request_id, text, **kwargs)
        turn = 'turn-' + request_id
        self.sessions[session_id].update(status='running', active_turn_id=turn)
        self.events.bind(session_id, session_id)
        self.events.append(session_id, {'kind': 'message_accepted', 'thread_id': session_id,
            'turn_id': turn, 'host_request_id': request_id, 'public': {'text': text}})
        def wire(method, **params):
            self.events.observer('runtime_to_host', {'method': method, 'params': {
                'threadId': session_id, 'turnId': turn, **params}}, runtime_instance_id='file-changes-fixture')
        wire('turn/started', turn={'id': turn, 'status': 'inProgress'})
        wire('item/completed', item={'id': 'progress', 'type': 'agentMessage', 'phase': 'commentary', 'text': '正在更新快捷命令与样式。'})
        wire('item/completed', item={'id': 'patch', 'type': 'fileChange', 'status': 'completed', 'changes': [
            {'path': '/fixture/frontend/src/composer-commands.tsx', 'diff': '-intermediate\n+ignored', 'kind': {'type': 'update'}}]})
        wire('turn/diff/updated', diff=DIFF)
        await self.events.flush()
        await asyncio.sleep(2)
        failed = text == 'changes-failed-fixture'
        if not failed:
            wire('item/completed', item={'id': 'answer', 'type': 'agentMessage', 'phase': 'final_answer', 'text': '已更新快捷命令与焦点样式，可以继续输入命令。'})
        wire('turn/completed', turn={'id': turn, 'status': 'failed' if failed else 'completed'})
        self.sessions[session_id].update(status='ready', active_turn_id=None)
        return {'request_id': request_id, 'turn_id': turn, 'status': 'accepted'}

if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as directory:
        events = ConsoleObservationService()
        host = ChangesHost(events, Path(directory))
        uvicorn.run(ConsoleApp(host, events, static_dir=os.environ.get('CONSOLE_BUILD_DIR')),
                    host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')
