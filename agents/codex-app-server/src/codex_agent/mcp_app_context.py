"""Durable, untrusted MCP App context, scoped to a Host session and view instance."""
from __future__ import annotations

import base64
import json
import re

from .state import identifier, payload_digest

MAX_CONTEXT_BYTES = 256 * 1024


def app_identity(body):
    values = [body.get(key) for key in ('server', 'uri', 'instanceId')]
    if any(not isinstance(value, str) or not value or len(value) > 2048 for value in values):
        raise ValueError('invalid MCP App identity')
    if not values[1].startswith('ui://'):
        raise ValueError('MCP App requires a ui resource')
    return 'mcp-app-' + payload_digest(values)


def context_path(session_id):
    return f'mcp-app-context/{identifier(session_id)}.json'


def update_context(store, session_id, body):
    source = app_identity(body)
    context = body.get('context')
    if not isinstance(context, dict) or set(context) - {'content', 'structuredContent'}:
        raise ValueError('invalid MCP App context')
    content = context.get('content', [])
    if not isinstance(content, list) or any(not isinstance(part, dict) or part.get('type') != 'text'
                                           or not isinstance(part.get('text'), str) for part in content):
        raise ValueError('MCP App context supports text and structured content')
    if 'structuredContent' in context and not isinstance(context['structuredContent'], dict):
        raise ValueError('invalid structured context')
    rows = store.read(context_path(session_id)) or {}
    if not content and not context.get('structuredContent'):
        rows.pop(source, None)
    else:
        rows[source] = {'server': body['server'], 'uri': body['uri'], 'instanceId': body['instanceId'],
                        'content': [{'type': 'text', 'text': part['text']} for part in content],
                        **({'structuredContent': context['structuredContent']} if 'structuredContent' in context else {})}
    encoded = json.dumps(rows, ensure_ascii=False, allow_nan=False)
    if len(rows) > 32 or len(encoded.encode()) > MAX_CONTEXT_BYTES:
        raise ValueError('MCP App context limit exceeded')
    store.write(context_path(session_id), rows)
    return {}


def with_app_context(store, session_id, options=None):
    """Use native additionalContext; never promote app data to developer instructions."""
    rows = store.read(context_path(session_id))
    if rows is None:
        return options
    return {**(options or {}), 'additionalContext': {
        **{key: value for key, value in ((options or {}).get('additionalContext') or {}).items()
           if not key.startswith('mcp-app-')},
        **{key: {'kind': 'untrusted', 'value': json.dumps(value, ensure_ascii=False, sort_keys=True)}
           for key, value in rows.items()},
    }}


def message_input(message):
    if not isinstance(message, dict) or message.get('role') != 'user':
        raise ValueError('MCP App messages must have the user role')
    content = message.get('content')
    if not isinstance(content, list) or not content or len(content) > 64:
        raise ValueError('invalid MCP App message content')
    if len(json.dumps(content, ensure_ascii=False).encode()) > 1024 * 1024:
        raise ValueError('MCP App message is too large')
    result = []
    for part in content:
        if not isinstance(part, dict):
            raise ValueError('invalid MCP App content block')
        kind = part.get('type')
        if kind == 'text' and isinstance(part.get('text'), str):
            result.append({'type': 'text', 'text': part['text'], 'text_elements': []})
        elif kind in {'image', 'audio'}:
            mime, data = part.get('mimeType'), part.get('data')
            if (not isinstance(mime, str) or not re.fullmatch(kind + r'/[a-zA-Z0-9.+-]+', mime)
                    or not isinstance(data, str) or not data):
                raise ValueError('invalid MCP App media')
            base64.b64decode(data, validate=True)
            result.append({'type': kind, 'url': f'data:{mime};base64,{data}'})
        else:
            raise ValueError('unsupported MCP App message modality')
    if all(part['type'] == 'text' and not part['text'].strip() for part in result):
        raise ValueError('empty MCP App message')
    return result
