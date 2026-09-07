def usage_delta(before, after):
    if not isinstance(after, dict) or type(after.get('total_tokens')) is not int or after['total_tokens'] < 0:
        return None
    if before is None:
        return after['total_tokens']
    if before.get('thread_id') != after.get('thread_id'):
        return None
    previous = before.get('total_tokens')
    if type(previous) is not int or after['total_tokens'] < previous:
        return None
    return after['total_tokens'] - previous
