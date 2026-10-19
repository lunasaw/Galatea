"""Minimal stdio MCP fixture. Only deterministic echo calls are supported."""
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    if 'id' not in message:
        continue
    method = message['method']
    if method == 'initialize':
        result = {'protocolVersion': message['params']['protocolVersion'],
                  'capabilities': {'tools': {}}, 'serverInfo': {'name': 'fixture', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'echo', 'description': 'Echo a synthetic fixture string.',
                  'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string'}},
                                  'required': ['text'], 'additionalProperties': False}}]}
    elif method == 'tools/call':
        result = {'content': [{'type': 'text', 'text': 'mcp-receipt:' + message['params']['arguments']['text']}],
                  'isError': False}
    elif method == 'ping':
        result = {}
    else:
        print(json.dumps({'jsonrpc': '2.0', 'id': message['id'],
                          'error': {'code': -32601, 'message': 'unknown fixture method'}}), flush=True)
        continue
    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
