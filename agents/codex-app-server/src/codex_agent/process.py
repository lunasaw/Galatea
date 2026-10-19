from __future__ import annotations

import asyncio
import os
from pathlib import Path
import signal
import uuid

from .config import AgentConfig
from .rpc import AppServerClient, JsonRpcPeer
from .state import AtomicJsonStore
from .stage0 import GateError


class ManagedProcess:
    def __init__(self, config: AgentConfig, *, observer=None, diagnostic=None):
        self.config, self.process, self.client = config, None, None
        self._store = None
        self.observer, self.diagnostic = observer, diagnostic

    async def start(self, server_request=None, *, notification=None, on_failure=None):
        self._store = AtomicJsonStore(self.config.state_dir)
        previous = self._store.read('runtime-process.json')
        if previous and previous.get('status') != 'stopped':
            if type(previous.get('pid')) is not int or previous['pid'] <= 1:
                raise GateError('unresolved runtime startup intent; operator reconciliation required')
            try:
                os.killpg(previous['pid'], 0)
            except ProcessLookupError:
                pass
            else:
                raise GateError('previous runtime process group still exists')
        self.config.codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        environment = self.config.child_environment()
        process_home = Path(environment["HOME"])
        process_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config.workspace_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
        self._store.write('runtime-process.json', {'status': 'starting'})
        self.process = await asyncio.create_subprocess_exec(
            str(self.config.binary), "app-server", "--listen", "stdio://", "--strict-config",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            cwd=self.config.workspace_dir, env=environment, start_new_session=True, limit=20 * 1024 * 1024)
        self._store.write('runtime-process.json', {'status': 'running', 'pid': self.process.pid})
        self.client = AppServerClient(JsonRpcPeer(self.process.stdout, self.process.stdin, server_request,
            notification=notification, on_failure=on_failure, observer=self.observer,
            diagnostic=self.diagnostic, runtime_instance_id=uuid.uuid4().hex))
        await self.client.initialize()
        if self.config.raw.get('codex', {}).get('model_api_key_file'):
            # app-server does not turn OPENAI_API_KEY into a logged-in account.
            # Use its native login API before any threads or model requests.
            await self.client.peer.request('account/login/start', {
                'type': 'apiKey', 'apiKey': environment['OPENAI_API_KEY'],
            })
        if self.config.skill_roots:
            for root in self.config.skill_roots:
                if not root.is_dir():
                    raise ValueError('configured skill root is not a directory')
            await self.client.peer.request('skills/extraRoots/set', {
                'extraRoots': [str(root) for root in self.config.skill_roots],
            })
        return self.client

    async def stop(self):
        if not self.process:
            return
        if self.client:
            await self.client.stop()
        if self.process.stdin:
            self.process.stdin.close()
        # Own the whole group, including helpers surviving a dead parent.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.process.pid, sig)
            except ProcessLookupError:
                break
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except asyncio.TimeoutError:
                continue
        await self.process.wait()
        if self._store:
            self._store.write('runtime-process.json', {'status': 'stopped', 'pid': self.process.pid})
        self.process = None
