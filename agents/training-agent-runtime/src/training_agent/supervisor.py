"""POSIX process-group ownership with conservative restart handling."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from .runtime.types import TurnOutcome

ALLOWED_ENV = {'TRAINING_CODEX_BIN','PATH','HOME','CODEX_HOME','LANG','LC_ALL','TMPDIR','OPENAI_API_KEY','GALATEA_MCP_TOKEN','SSL_CERT_FILE','HTTPS_PROXY','HTTP_PROXY','ALL_PROXY','NO_PROXY','https_proxy','http_proxy','all_proxy','no_proxy'}

class Supervisor:
    def __init__(self, root, environment, timeout=300, grace=5, inference_check=None):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.environment={k:v for k,v in environment.items() if k in ALLOWED_ENV}
        self.timeout,self.grace=timeout,grace
        self.marker=self.root/'worker.json'
        self.process=None
        self.cancel_check=lambda:False
        self.inference_check=inference_check or (lambda:None)

    def ensure_clean(self):
        if self.marker.exists():
            pgid=json.loads(self.marker.read_text())['pgid']
            if type(pgid) is not int or pgid <= 1: raise RuntimeError('Invalid worker identity')
            try: os.killpg(pgid,0)
            except ProcessLookupError: self.marker.unlink(); return
            raise RuntimeError('Old execution group not proven dead; operator reconciliation required')

    def recover_identity(self, state):
        path=self.root/'identity.json'
        if not path.exists(): return None
        value=json.loads(path.read_text())
        if any(value.get(k)!=state[k] for k in ('campaign_id','decision_seq')):
            raise ValueError('Worker journal identity mismatch')
        return value['thread_id'],value['turn_id']

    def interrupt(self):
        if self.process is not None and self.process.poll() is None:
            os.kill(self.process.pid,signal.SIGUSR1)

    def execute(self, command, input_text=None):
        self.ensure_clean()
        # Start gate prevents app-server launch before durable group identity exists.
        gate=self.root/'start.gate'
        gate.unlink(missing_ok=True)
        wrapper='import os,sys,time; p=sys.argv[1];\nwhile not os.path.exists(p): time.sleep(.01)\nos.execv(sys.argv[2],sys.argv[2:])'
        self.process=subprocess.Popen([sys.executable,'-c',wrapper,str(gate),*command],
            env=self.environment,start_new_session=True,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        pgid=self.process.pid
        with self.marker.open('w') as handle:
            json.dump({'pgid':pgid},handle); handle.flush(); os.fsync(handle.fileno())
        gate.touch()
        try:
            deadline=time.monotonic()+self.timeout
            pending_input=input_text
            while True:
                if self.cancel_check():
                    self.interrupt()
                    try: self.process.communicate(timeout=self.grace)
                    except subprocess.TimeoutExpired: pass
                    raise InterruptedError('Worker cancellation requested')
                remaining=deadline-time.monotonic()
                if remaining <= 0:
                    self.interrupt()
                    try: self.process.communicate(timeout=self.grace)
                    except subprocess.TimeoutExpired: pass
                    raise TimeoutError('Worker turn deadline exceeded')
                try:
                    stdout,stderr=self.process.communicate(pending_input,timeout=min(.2,remaining))
                    break
                except subprocess.TimeoutExpired:
                    pending_input=None
            if self.process.returncode: raise RuntimeError('Worker failed: '+str(self.process.returncode))
            return subprocess.CompletedProcess(command,self.process.returncode,stdout,stderr)
        finally:
            try: os.killpg(pgid,signal.SIGTERM)
            except ProcessLookupError: pass
            try: self.process.wait(timeout=self.grace)
            except subprocess.TimeoutExpired: pass
            try: os.killpg(pgid,signal.SIGKILL)
            except ProcessLookupError: pass
            self.process.wait()
            self.process=None
            gate.unlink(missing_ok=True)
            self.ensure_clean()

    def run_turn(self, request, save_identity):
        self.inference_check()
        with tempfile.TemporaryDirectory(dir=self.root,prefix='turn-') as directory:
            folder=Path(directory)
            payload=folder/'request.json'; result=folder/'result.json'; identity=self.root/'identity.json'
            identity.unlink(missing_ok=True)
            payload.write_text(json.dumps(asdict(request)))
            try:
                self.execute([sys.executable,'-m','training_agent.runtime.worker',str(payload),str(result),str(identity)])
            finally:
                if identity.exists():
                    saved=json.loads(identity.read_text()); save_identity(saved['thread_id'],saved['turn_id'])
            return TurnOutcome(**json.loads(result.read_text()))
