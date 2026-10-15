"""Correlate only explicit context and protocol identities, never recent sessions."""


def object_value(value):
    return value if isinstance(value, dict) else {}


class Correlator:
    def __init__(self):
        self.pending, self.threads, self.turns = {}, {}, {}

    def process(self, entry):
        payload, direction = entry['payload'], entry['direction']
        runtime = entry['runtime_instance_id']
        context = dict(entry['context'])
        method = payload.get('method')
        kind = entry['kind'] or ('request' if 'id' in payload else 'notification')
        rpc_id = payload.get('id')
        peer = 'host_to_runtime' if direction == 'runtime_to_host' else 'runtime_to_host'
        prior = {}
        if entry['source'] == 'codex' and 'id' in payload and not method:
            kind = 'response'
            prior = self.pending.pop((runtime, peer, type(rpc_id).__name__, rpc_id), {})
            method = prior.get('method')
            context = {**prior, **context}
        params, result = object_value(payload.get('params')), object_value(payload.get('result'))
        ids = {}
        for name in ('thread', 'turn', 'item', 'call'):
            direct = params.get(name + 'Id')
            nested = object_value(params.get(name)).get('id') or object_value(result.get(name)).get('id')
            ids[name + '_id'] = direct or nested or context.get(name + '_id')
        if object_value(params.get('item')).get('type') == 'dynamicToolCall':
            ids['call_id'] = ids['item_id']
        thread, turn = ids['thread_id'], ids['turn_id']
        session = context.get('session_id') or self.threads.get(thread)
        request = context.get('host_request_id') or self.turns.get((thread, turn))
        bindings = []
        if thread and session and self.threads.get(thread) != session:
            self.threads[thread] = session
            bindings.append({'thread_id': thread, 'session_id': session})
        if thread and turn and request:
            self.turns[(thread, turn)] = request
        row = {'source': entry['source'], 'direction': direction, 'runtime_instance_id': runtime,
               'observed_at': entry['observed_at'], 'kind': kind, 'method': method, 'rpc_id': rpc_id,
               'session_id': session, 'host_request_id': request, **ids,
               'correlation': 'bound' if session else 'unbound'}
        if kind == 'request':
            self.pending[(runtime, direction, type(rpc_id).__name__, rpc_id)] = row
        if kind == 'runtime_closed':
            self.pending = {key: value for key, value in self.pending.items() if key[0] != runtime}
        return row, bindings
