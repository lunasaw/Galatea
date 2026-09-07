"""Single-turn process. Identity journal is part of the durable Runner volume."""
import json
import os
import signal
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from .types import TurnRequest
from .codex import Adapter

def atomic_json(path,value):
    path=Path(path)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.journal-')
    try:
        with os.fdopen(fd,'w') as handle:
            json.dump(value,handle,allow_nan=False); handle.flush(); os.fsync(handle.fileno())
        os.replace(name,path)
        directory=os.open(path.parent,os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(name): os.unlink(name)

def run(payload,result,identity,adapter=None):
    request=TurnRequest(**json.loads(Path(payload).read_text()))
    adapter=adapter or Adapter(codex_bin=os.environ.get("TRAINING_CODEX_BIN"))
    signal.signal(signal.SIGUSR1,lambda *_:adapter.interrupt())
    def save(thread,turn):
        atomic_json(identity,{'campaign_id':request.campaign_id,'decision_seq':request.decision_seq,
                              'thread_id':thread,'turn_id':turn})
    outcome=adapter.run_turn(request,save)
    atomic_json(result,asdict(outcome))

if __name__=='__main__': run(*map(Path,sys.argv[1:4]))
