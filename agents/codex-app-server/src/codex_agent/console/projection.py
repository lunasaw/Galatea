"""Public content projection and replayable incremental conversation changes."""
import json
import re
from urllib.parse import urlsplit, parse_qs

SECRET = re.compile(r'password|secret|api.?key|authorization|cookie|credential|private.?key|encrypted|[_-]token$|^(?:(?:access|refresh|id|auth|session|bearer|oauth)[_-]?)?token$', re.I)
PRIVATE = {'systeminstructions', 'developerinstructions', 'baseinstructions', 'instructions',
           'systemprompt', 'developerprompt', 'reasoning', 'privatereasoning',
           'testsample', 'testlabel', 'rawlog', 'artifactcontent', 'signedurl'}


def redact(value, *, method=None):
    marks = []
    def omit(path, reason):
        marks.append({'path': path, 'reason': reason})
        return '[redacted:' + reason + ']'
    def walk(item, path):
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                pointer = path + '/' + key.replace('~', '~0').replace('/', '~1')
                normalized = re.sub(r'[_-]', '', key).lower()
                reason = None
                if SECRET.search(key):
                    reason = 'credential'
                elif method == 'item/tool/requestUserInput' and key == 'answers':
                    reason = 'user_input'
                elif normalized in PRIVATE or normalized.rstrip('s') in PRIVATE:
                    reason = 'private_content'
                elif item.get('role') in {'system', 'developer'} and key in {'content', 'text'}:
                    reason = 'private_instructions'
                elif item.get('type') == 'reasoning' and key not in {'id', 'type', 'summary'}:
                    reason = 'private_reasoning'
                elif method == 'item/reasoning/textDelta' and key == 'delta':
                    reason = 'private_reasoning'
                result[key] = omit(pointer, reason) if reason else walk(child, pointer)
            return result
        if isinstance(item, list):
            return [walk(child, path + '/' + str(i)) for i, child in enumerate(item)]
        if isinstance(item, str):
            if item.lstrip().startswith(('{', '[')):
                try:
                    decoded = json.loads(item)
                except ValueError:
                    pass
                else:
                    return json.dumps(walk(decoded, path), ensure_ascii=False, separators=(',', ':'))
            if item.startswith(('https://', 'http://')):
                parsed = urlsplit(item)
                if parsed.password or any(SECRET.search(key) or key.lower() in {'x-amz-signature', 'signature', 'sig', 'x-goog-signature'} for key in parse_qs(parsed.query)):
                    return omit(path, 'signed_url')
        return item
    return walk(value, ''), marks


class Projector:
    def __init__(self):
        self.user_items = {}

    def changes(self, event):
        output = []
        scope = {key: event.get(key) for key in ('session_id', 'thread_id')}
        if not any(scope.values()):
            return output
        payload = event['payload']
        params = payload.get('params', {})
        params = params if isinstance(params, dict) else {}
        item = params.get('item', {})
        item = item if isinstance(item, dict) else {}
        public = payload.get('public', {})
        turn, thread, item_id = event.get('turn_id'), event.get('thread_id'), event.get('item_id')
        request, call = event.get('host_request_id'), event.get('call_id')
        method, kind = event.get('method'), event['kind']
        def change(view, key, values=None, append=None, remove=False):
            output.append({**scope, 'seq': event['seq'], 'view': view, 'key': key,
                           'set': values or {}, 'append': append or {}, 'remove': remove})
        common = {'turn_id': turn, 'thread_id': thread, 'source_event_id': event['event_id']}
        message_key = f'message:{thread}:{turn}:{item_id}'
        if kind == 'thread_restored':
            # Native rollout is the durable conversation authority. Reset only
            # this chat's projection, leaving the event audit log intact.
            for view in ('messages', 'steps', 'turns'):
                change(view, '__reset__', {'reset_view': True})
            for native_turn in public.get('thread', {}).get('turns', []):
                tid = native_turn['id']
                change('turns', tid, {**common, 'turn_id': tid, 'status': native_turn.get('status')})
                for native_item in native_turn.get('items', []):
                    restored = {**event, 'kind': 'notification', 'method': 'item/completed', 'turn_id': tid,
                                'host_request_id': None, 'item_id': native_item['id'],
                                'payload': {'params': {'item': native_item}}}
                    output.extend(self.changes(restored))
            return output
        if kind == 'message_accepted' and request:
            change('messages', 'request:' + request, {**common, 'role': 'user', 'text': public.get('text', ''),
                                                     'content': public.get('content'), 'request_id': request})
        if method == 'turn/start' and kind == 'response' and turn and request:
            change('messages', 'request:' + request, {**common, 'request_id': request})
            for key in self.user_items.pop((thread, turn), []):
                change('messages', key, remove=True)
        if method == 'item/agentMessage/delta' and item_id:
            change('messages', message_key, {**common, 'role': 'assistant', 'item_id': item_id}, {'text': params.get('delta', '')})
        if method in {'item/started', 'item/completed'} and item_id:
            item_type = item.get('type')
            if item_type == 'agentMessage':
                change('messages', message_key, {**common, 'role': 'assistant', 'item_id': item_id, 'text': item.get('text', '')})
            elif item_type == 'userMessage':
                key = 'request:' + request if request else message_key
                text = '\n'.join(c.get('text', '') for c in item.get('content', []) if isinstance(c, dict) and c.get('type') == 'text')
                change('messages', key, {**common, 'role': 'user', 'item_id': item_id, 'text': text, 'content': item.get('content')})
                if not request:
                    self.user_items.setdefault((thread, turn), []).append(key)
            elif item_type == 'reasoning':
                values = {**common, 'type': 'reasoning', 'item_id': item_id, 'detail': item,
                          'status': 'completed' if method == 'item/completed' else 'inProgress'}
                values['completed_at' if method == 'item/completed' else 'started_at'] = event['observed_at']
                change('steps', f'item:{thread}:{turn}:{item_id}', values)
            elif item_type != 'dynamicToolCall':
                change('steps', f'item:{thread}:{turn}:{item_id}', {**common, 'type': item_type, 'detail': item,
                       'status': item.get('status', 'completed' if method == 'item/completed' else 'inProgress')})
        if (method == 'item/tool/call' or item.get('type') == 'dynamicToolCall' or kind in {'tool_call', 'tool_result'}):
            call = call or public.get('call_id')
            if call:
                values = {**common, 'type': 'tool', 'call_id': call, 'observed_at': event['observed_at']}
                detail = item or params or public
                for key in ('tool', 'arguments', 'status', 'success', 'operation_id'):
                    if key in detail:
                        values[key] = detail[key]
                result = payload.get('result')
                if isinstance(result, dict):
                    values['result'] = result
                    values['success'] = result.get('success')
                    for content in result.get('contentItems', []):
                        try:
                            envelope = json.loads(content.get('text', ''))
                        except (ValueError, TypeError):
                            continue
                        data = envelope.get('data') if isinstance(envelope, dict) else None
                        if isinstance(data, dict):
                            values['operation_id'] = data.get('operation_id')
                            if data.get('job_id') or data.get('job'):
                                values['job'] = data.get('job', data)
                change('steps', f'tool:{thread}:{turn}:{call}', values)
        if method in {'turn/started', 'turn/completed'} and turn:
            change('turns', turn, {**common, 'status': params.get('turn', {}).get('status', 'unknown'), 'detail': params.get('turn')})
        if method == 'thread/tokenUsage/updated':
            change('turns', turn or 'thread:' + str(thread), {**common, 'usage': params.get('tokenUsage')})
        if method == 'item/reasoning/summaryTextDelta' and item_id:
            # A summary has independently streamed paragraphs. Reuse the native
            # item identity so the completed summary replaces, rather than
            # duplicates, the live preview. Private textDelta is never projected.
            index = params.get('summaryIndex', 0)
            delta = params.get('delta')
            if isinstance(index, int) and index >= 0 and isinstance(delta, str):
                change('steps', f'item:{thread}:{turn}:{item_id}',
                       {**common, 'type': 'reasoning', 'item_id': item_id},
                       {f'summary_{index}': delta})
        if method in {'turn/plan/updated', 'item/plan/delta', 'warning', 'error'}:
            key = f'{method}:{turn}:{item_id}'
            values = {**common, 'type': method, 'detail': params}
            change('steps', key, values, {'text': params['delta']} if isinstance(params.get('delta'), str) else None)
        if method in {'item/commandExecution/outputDelta', 'item/fileChange/outputDelta'} and item_id:
            change('steps', f'item:{thread}:{turn}:{item_id}', {**common}, {'text': params.get('delta', '')})
        if kind == 'request' and method != 'item/tool/call':
            change('steps', 'request:' + event['event_id'], {**common, 'type': 'request', 'method': method,
                   'detail': payload, 'interactive': event.get('direction') == 'runtime_to_host'})
        return output
