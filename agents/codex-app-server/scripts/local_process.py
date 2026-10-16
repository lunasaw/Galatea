"""Own one foreground source instance; never kill an arbitrary port owner."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def identity(pid):
    try:
        # /proc's start time distinguishes the process from a reused PID.
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return {'pid': pid, 'started': fields[19]}
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None


def descendants(pid):
    result = []
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            fields = (process / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[1]) == pid:
                child = identity(int(process.name))
                if child:
                    result.extend(descendants(child['pid']))
                    result.append(child)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return result


def send(record, sig):
    if record and identity(record['pid']) == record:
        try:
            os.kill(record['pid'], sig)
        except ProcessLookupError:
            pass


def remove_record(path, record):
    try:
        if json.loads(path.read_text()) == record:
            path.unlink(missing_ok=True)
    except FileNotFoundError:
        pass


def stop(project):
    path = project / '.local/console.pid'
    try:
        record = json.loads(path.read_text())
    except FileNotFoundError:
        return
    if not record:
        remove_record(path, record)
        return
    if identity(record['pid']) == record:
        command = Path(f'/proc/{record["pid"]}/cmdline').read_bytes().split(b'\0')
        if b'codex_agent.cli' not in command or str(project / '.local/agent.json').encode() not in command:
            raise SystemExit('PID file does not identify this project; refusing to stop another process.')
        print(f'Stopping previous project instance (PID {record["pid"]})...', flush=True)
        children = descendants(record['pid'])
        send(record, signal.SIGTERM)
        deadline = time.monotonic() + 15
        while identity(record['pid']) == record and time.monotonic() < deadline:
            time.sleep(.1)
        send(record, signal.SIGKILL)
        # The native child owns a separate process group. Clean up only captured
        # descendants if a forced stop prevented the Host's normal cleanup.
        for child in children:
            send(child, signal.SIGTERM)
        deadline = time.monotonic() + 3
        while any(identity(c['pid']) == c for c in children) and time.monotonic() < deadline:
            time.sleep(.1)
        for child in children:
            send(child, signal.SIGKILL)
    remove_record(path, record)


def run(project, command):
    path = project / '.local/console.pid'
    child = subprocess.Popen(command, cwd=project, start_new_session=True)
    record = identity(child.pid)
    if record is None:
        return child.wait()
    path.write_text(json.dumps(record))
    # Serialize setup/restart, then allow the next invocation to restart us.
    try:
        os.close(9)
    except OSError:
        pass

    def forward(sig, _frame):
        send(record, sig)

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, forward)
    try:
        code = child.wait()
    finally:
        if child.poll() is None:
            send(record, signal.SIGTERM)
            child.wait(timeout=20)
        remove_record(path, record)
    return code if code >= 0 else 128 - code


if __name__ == '__main__':
    operation, directory, *command = sys.argv[1:]
    project = Path(directory).resolve()
    if operation == 'stop':
        stop(project)
    elif operation == 'run':
        raise SystemExit(run(project, command))
    else:
        raise SystemExit('Expected stop or run')
