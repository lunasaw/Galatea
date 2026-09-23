from __future__ import annotations

import asyncio
import os
from pathlib import Path
import signal

from .config import AgentConfig
from .rpc import AppServerClient, JsonRpcPeer
from .state import AtomicJsonStore
from .stage0 import GateError


class ManagedProcess:
    def __init__(self, config: AgentConfig):
        self.config, self.process, self.client = config, None, None
        self._store = None

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
        process_home = Path(self.config.child_environment()["HOME"])
        process_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config.workspace_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
        self._store.write('runtime-process.json', {'status': 'starting'})
        self.process = await asyncio.create_subprocess_exec(
            str(self.config.binary), "app-server", "--listen", "stdio://", "--strict-config",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            cwd=self.config.workspace_dir, env=self.config.child_environment(), start_new_session=True, limit=4 * 1024 * 1024)
        self._store.write('runtime-process.json', {'status': 'running', 'pid': self.process.pid})
        self.client = AppServerClient(JsonRpcPeer(self.process.stdout, self.process.stdin, server_request,
                                                       notification=notification, on_failure=on_failure))
        await self.client.initialize()
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
