"""The runtime and exported protocol must advance together."""
from pathlib import Path

VERSION = '0.163.0'
RUNTIME_VERSION = 'codex-cli ' + VERSION
PROTOCOL_PATH = Path(__file__).parent / 'protocol' / (VERSION + '.json')
