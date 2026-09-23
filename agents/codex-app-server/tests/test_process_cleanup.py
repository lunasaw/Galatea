import asyncio
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from codex_agent.process import ManagedProcess


class ProcessCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_owned_process_group_is_killed_after_parent_exits(self):
        import os
        import sys
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / 'helper.pid'
            code = ('import subprocess, pathlib; '
                    'p=subprocess.Popen(["/usr/bin/sleep","120"]); '
                    f'pathlib.Path({str(pid_file)!r}).write_text(str(p.pid))')
            parent = await asyncio.create_subprocess_exec(sys.executable, '-c', code, start_new_session=True,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            managed = ManagedProcess(SimpleNamespace())
            managed.process = parent
            await parent.wait()
            pid = int(pid_file.read_text())
            try:
                await managed.stop()
                async with asyncio.timeout(3):
                    while Path(f'/proc/{pid}/stat').exists():
                        try:
                            state = Path(f'/proc/{pid}/stat').read_text().split()[2]
                        except FileNotFoundError:
                            break
                        if state == 'Z':
                            break
                        await asyncio.sleep(.01)
            finally:
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass

    async def test_unresolved_process_intent_blocks_new_runtime(self):
        from codex_agent.state import AtomicJsonStore
        from codex_agent.stage0 import GateError
        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(state_dir=Path(directory))
            AtomicJsonStore(config.state_dir).write('runtime-process.json', {'status':'starting'})
            with self.assertRaises(GateError):
                await ManagedProcess(config).start()
