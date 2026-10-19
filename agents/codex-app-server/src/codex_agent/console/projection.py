"""Public content projection and replayable incremental conversation changes."""
import json
import re
from urllib.parse import urlsplit, parse_qs

SECRET = re.compile(r'password|secret|api.?key|authorization|cookie|credential|private.?key|encrypted|[_-]token$|^(?:(?:access|refresh|id|auth|session|bearer|oauth)[_-]?)?token$', re.I)
PRIVATE = {'systeminstructions', 'developerinstructions', 'baseinstructions', 'instructions',
           'systemprompt', 'developerprompt', 'reasoning', 'privatereasoning',
           'testsample', 'testlabel', 'rawlog', 'artifactcontent', 'signedurl'}
PUBLIC_CREDENTIAL_STORE_MODES = {
    'cli_auth_credentials_store': {'file', 'keyring', 'auto', 'ephemeral'},
    'cliAuthCredentialsStore': {'file', 'keyring', 'auto', 'ephemeral'},
    'mcp_oauth_credentials_store': {'file', 'keyring', 'auto'},
}


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
                # Config origin keys name settings; their values are source
                # metadata, not those settings' contents. Still walk metadata
                # recursively so any nested private fields remain protected.
                config_origin = (method == 'config/read' and path in {'/origins', '/result/origins'}
                                 and isinstance(child, dict) and isinstance(child.get('name'), dict)
                                 and isinstance(child['name'].get('type'), str)
                                 and isinstance(child.get('version'), str))
                store_mode = (method in {'config/read', 'configRequirements/read'}
                              and key in PUBLIC_CREDENTIAL_STORE_MODES
                              and (child is None or isinstance(child, str)
                                   and child in PUBLIC_CREDENTIAL_STORE_MODES[key]))
                if config_origin or store_mode:
                    pass
                elif SECRET.search(key) or key == 'io.codex.host/call-proof':
                    reason = 'credential'
                elif method == 'account/gatewayOAuth/changed' and key == 'authUrl' and child is not None:
                    reason = 'authorization_handoff'
                elif method == 'item/tool/requestUserInput' and key == 'answers':
                    reason = 'user_input'
                elif normalized in PRIVATE or normalized.rstrip('s') in PRIVATE:
                    reason = 'private_content'
                elif item.get('role') in ('system', 'developer') and key in {'content', 'text'}:
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
        self.request_user_items = {}

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
                change('turns', tid, {**common, 'turn_id': tid, 'status': native_turn.get('status'),
                       'detail': {k: v for k, v in native_turn.items() if k != 'items'}})
                for native_item in native_turn.get('items', []):
                    restored = {**event, 'kind': 'notification', 'method': 'item/completed', 'turn_id': tid,
                                'host_request_id': None, 'item_id': native_item['id'],
                                'history_replay': True,
                                'payload': {'params': {'item': native_item}}}
                    output.extend(self.changes(restored))
            return output
        if kind == 'message_accepted' and request:
            change('messages', 'request:' + request, {**common, 'role': 'user', 'text': public.get('text', ''),
                                                     'content': public.get('content'), 'request_id': request})
        if method == 'turn/start' and kind == 'response' and turn and request:
            change('messages', 'request:' + request, {**common, 'request_id': request})
            # Only the initial native input aliases the Host's turn/start.
            # Steered inputs share the turn but each has its own item identity.
            for key, native_id in self.user_items.pop((thread, turn), [])[:1]:
                self.request_user_items[(thread, request)] = native_id
                change('messages', 'request:' + request, {'item_id': native_id})
                change('messages', key, remove=True)
        if method == 'item/agentMessage/delta' and item_id:
            change('messages', message_key, {**common, 'role': 'assistant', 'item_id': item_id}, {'text': params.get('delta', '')})
        if method in {'item/started', 'item/completed'} and item_id:
            item_type = item.get('type')
            if item_type == 'agentMessage':
                change('messages', message_key, {**common, 'role': 'assistant', 'item_id': item_id,
                       'text': item.get('text', ''), 'phase': item.get('phase'),
                       **{key: item[key] for key in ('delivery', 'questions', 'memoryCitation') if key in item},
                       'status': 'completed' if method == 'item/completed' else 'inProgress'})
            elif item_type == 'userMessage':
                client_id = item.get('clientId')
                initial = not client_id and request and self.request_user_items.setdefault((thread, request), item_id) == item_id
                key = 'request:' + request if initial else message_key
                text = '\n'.join(c.get('text', '') for c in item.get('content', []) if isinstance(c, dict) and c.get('type') == 'text')
                # Keep native identity and carry the client submission identity
                # for optimistic steering reconciliation, also during replay.
                change('messages', key, {**common, 'role': 'user', 'item_id': item_id,
                       'client_id': client_id, 'text': text, 'content': item.get('content')})
                if not request and not client_id:
                    pending = self.user_items.setdefault((thread, turn), [])
                    if (key, item_id) not in pending:
                        pending.append((key, item_id))
            elif item_type == 'reasoning':
                values = {**common, 'type': 'reasoning', 'item_id': item_id, 'detail': item,
                          'status': 'completed' if method == 'item/completed' else 'inProgress'}
                if not event.get('history_replay'):
                    values['completed_at' if method == 'item/completed' else 'started_at'] = event['observed_at']
                change('steps', f'item:{thread}:{turn}:{item_id}', values)
            elif item_type != 'dynamicToolCall':
                change('steps', f'item:{thread}:{turn}:{item_id}', {**common, 'type': item_type, 'detail': item,
                       'status': item.get('status', 'completed' if method == 'item/completed' else 'inProgress')})
        if (method == 'item/tool/call' or item.get('type') == 'dynamicToolCall' or kind in {'tool_call', 'tool_result'}):
            call = call or public.get('call_id') or (item_id if item.get('type') == 'dynamicToolCall' else None)
            if call:
                values = {**common, 'type': 'tool', 'call_id': call, 'observed_at': event['observed_at']}
                detail = item or params or public
                for key in ('tool', 'arguments', 'status', 'success', 'operation_id'):
                    if key in detail:
                        values[key] = detail[key]
                if item:
                    values['detail'] = item
                    if item.get('contentItems') is not None:
                        values['result'] = {'contentItems': item['contentItems'], 'success': item.get('success')}
                result = payload.get('result')
                if isinstance(result, dict):
                    values['result'] = result
                    values['success'] = result.get('success')
                change('steps', f'tool:{thread}:{turn}:{call}', values)
        if method in {'turn/started', 'turn/completed'} and turn:
            change('turns', turn, {**common, 'status': params.get('turn', {}).get('status', 'unknown'), 'detail': params.get('turn'),
                   'started_at' if method == 'turn/started' else 'completed_at': event['observed_at']})
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
        if method == 'item/plan/delta' and item_id:
            change('steps', f'item:{thread}:{turn}:{item_id}', {**common, 'type': 'plan'},
                   {'text': params['delta']} if isinstance(params.get('delta'), str) else None)
        if method == 'item/mcpToolCall/progress' and item_id and isinstance(params.get('message'), str):
            change('steps', f'item:{thread}:{turn}:{item_id}', {**common, 'type': 'mcpToolCall'},
                   {'text': params['message'] + '\n'})
        if method in {'turn/plan/updated', 'warning', 'error'}:
            key = f'{method}:{turn}:{item_id}'
            values = {**common, 'type': method, 'detail': params}
            change('steps', key, values, {'text': params['delta']} if isinstance(params.get('delta'), str) else None)
        if method == 'item/fileChange/patchUpdated' and item_id:
            change('steps', f'item:{thread}:{turn}:{item_id}', {**common, 'type': 'fileChange',
                   'detail': {'type': 'fileChange', 'id': item_id, 'changes': params.get('changes', [])}})
        if method == 'turn/diff/updated':
            change('steps', f'diff:{thread}:{turn}', {**common, 'type': 'turnDiff', 'detail': params})
        if method in {'guardianWarning', 'autoApprovalReview/strictReviewRequired', 'model/rerouted',
                      'model/verification', 'modelProvider/authRecoveryStarted', 'modelProvider/authRecoveryCompleted',
                      'model/safetyBuffering/updated'}:
            change('steps', 'notice:' + event['event_id'], {**common, 'type': 'warning',
                   'detail': {'method': method, **params}})
        if method in {'hook/started', 'hook/completed'}:
            run = params.get('run', {})
            change('steps', f"hook:{thread}:{run.get('id', event['event_id'])}",
                   {**common, 'type': 'hook', 'detail': run,
                    'status': run.get('status', 'completed' if method == 'hook/completed' else 'inProgress')})
        if method in {'item/autoApprovalReview/started', 'item/autoApprovalReview/completed'}:
            change('steps', f"guardian:{thread}:{params['reviewId']}",
                   {**common, 'type': 'guardian', 'detail': params,
                    'status': params.get('review', {}).get('status', 'unknown')})
        if method in {'item/commandExecution/outputDelta', 'item/fileChange/outputDelta'} and item_id:
            change('steps', f'item:{thread}:{turn}:{item_id}', {**common}, {'text': params.get('delta', '')})
        if kind == 'request' and method != 'item/tool/call':
            change('steps', 'request:' + event['event_id'], {**common, 'type': 'request', 'method': method,
                   'detail': payload, 'interactive': event.get('direction') == 'runtime_to_host'})
        return output
