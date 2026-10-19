#!/usr/bin/env python3
"""Reproduce the Host protocol bundle from its pinned native executable."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from codex_agent.runtime_version import PROTOCOL_PATH, RUNTIME_VERSION


def export(binary, output):
    version = subprocess.check_output([str(binary), '--version'], text=True, timeout=15).strip()
    if version != RUNTIME_VERSION:
        raise ValueError(f'expected {RUNTIME_VERSION}, got {version}')
    with tempfile.TemporaryDirectory(prefix='codex-protocol-') as directory:
        subprocess.run([str(binary), 'app-server', 'generate-json-schema', '--experimental',
                        '--out', directory], check=True, capture_output=True, timeout=30)
        root = Path(directory)
        def read(name):
            return json.loads((root / (name + '.json')).read_text())
        server = read('ServerRequest')
        responses = {}
        for row in server['oneOf']:
            method = row['properties']['method']['enum'][0]
            params = row['properties']['params']['$ref'].rsplit('/', 1)[1]
            if not params.endswith('Params'):
                raise ValueError(f'unknown response schema for {method}')
            responses[method] = read(params.removesuffix('Params') + 'Response')
        bundle = {'version': version, 'client': read('ClientRequest'), 'server': server,
                  'responses': responses, 'notifications': read('ServerNotification')}
    output.write_text(json.dumps(bundle, ensure_ascii=False, separators=(',', ':')) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=PROTOCOL_PATH)
    args = parser.parse_args()
    export(args.binary.resolve(), args.output)
