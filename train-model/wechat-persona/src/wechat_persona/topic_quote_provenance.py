"""Read export metadata without decoding any message content, including test text."""
from __future__ import annotations

from collections import Counter
import json
import mmap
from pathlib import Path
import re

from ._common import file_digest
from .topic_candidates import read_json, rows
from .topic_context import TopicContractError


METADATA_KEYS = frozenset({'id', 'serverId', 'renderType', 'type', 'quoteServerId',
                           'message_id', 'source_record_index', 'reply_to', 'message_kind'})
STRING = re.compile(rb'"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"')
TOKEN = re.compile(rb'"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"|[{}\[\]]')
SCALAR = re.compile(rb'(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)')


def whitespace(data, index: int) -> int:
    while index < len(data) and data[index] in b' \t\r\n':
        index += 1
    return index


def value_end(data, start: int) -> int:
    """Skip values lexically; never pass an unselected value to a JSON decoder."""
    start = whitespace(data, start)
    if start >= len(data):
        raise TopicContractError('truncated metadata source')
    char = data[start]
    if char == ord('"'):
        found = STRING.match(data, start)
        if not found:
            raise TopicContractError('invalid metadata string')
        return found.end()
    if char not in b'{[':
        found = SCALAR.match(data, start)
        if not found:
            raise TopicContractError('invalid metadata scalar')
        return found.end()
    stack = [ord('}') if char == ord('{') else ord(']')]
    for found in TOKEN.finditer(data, start + 1):
        char = data[found.start()]
        if char == ord('"'):
            continue
        if char in b'{[':
            stack.append(ord('}') if char == ord('{') else ord(']'))
        elif not stack or char != stack.pop():
            raise TopicContractError('unbalanced metadata value')
        elif not stack:
            return found.end()
    raise TopicContractError('truncated metadata container')


def object_fields(data, start: int = 0):
    index = whitespace(data, start)
    if data[index:index + 1] != b'{':
        raise TopicContractError('metadata object required')
    index = whitespace(data, index + 1)
    seen = set()
    if data[index:index + 1] == b'}':
        return
    while index < len(data):
        key_end = value_end(data, index)
        if data[index] != ord('"'):
            raise TopicContractError('metadata key must be a string')
        key = json.loads(data[index:key_end])
        if key in seen:
            raise TopicContractError('duplicate metadata key')
        seen.add(key)
        index = whitespace(data, key_end)
        if data[index:index + 1] != b':':
            raise TopicContractError('metadata field separator missing')
        begin = whitespace(data, index + 1)
        end = value_end(data, begin)
        yield key, begin, end
        index = whitespace(data, end)
        if data[index:index + 1] == b'}':
            return
        if data[index:index + 1] != b',':
            raise TopicContractError('metadata field delimiter missing')
        index = whitespace(data, index + 1)
    raise TopicContractError('truncated metadata object')


def metadata_object(data, start: int = 0) -> dict:
    result = {}
    for key, begin, end in object_fields(data, start):
        if key in METADATA_KEYS:
            if data[begin] in b'{[':
                raise TopicContractError('metadata value must be scalar')
            result[key] = json.loads(data[begin:end])
    return result


def export_metadata(path: Path):
    with path.open('rb') as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        found_messages = False
        for key, begin, end in object_fields(data):
            if key != 'messages':
                continue
            found_messages = True
            if data[begin] != ord('['):
                raise TopicContractError('export messages array required')
            index, ordinal = whitespace(data, begin + 1), 0
            while data[index:index + 1] != b']':
                item_end = value_end(data, index)
                # JsonImporter enumerates streamed messages from zero.
                yield ordinal, metadata_object(data, index)
                ordinal += 1
                index = whitespace(data, item_end)
                if data[index:index + 1] == b']':
                    break
                if data[index:index + 1] != b',':
                    raise TopicContractError('export message delimiter missing')
                index = whitespace(data, index + 1)
                if data[index:index + 1] == b']':
                    raise TopicContractError('export trailing comma')
            if index + 1 != end:
                raise TopicContractError('export array boundary changed')
        if not found_messages:
            raise TopicContractError('export missing messages')


def audit_quotes(*, raw: Path, source: Path, consent: Path) -> dict:
    manifest = read_json(source / 'manifests/source_manifest.json')
    if (file_digest(raw) != manifest['source_sha256']
            or file_digest(consent) != manifest['consent_file_sha256']):
        raise TopicContractError('quote audit source or consent changed')
    split = read_json(source / 'manifests/split_manifest.json')
    train_sessions = set(split['session_ids_by_split']['train'])
    train_ids = {mid for row in rows(source / 'manifests/lineage.jsonl')
                 if row['stage'] == 'session' and row['object_id'] in train_sessions
                 for mid in row['source_message_ids']}
    retained, normalized_kinds, normalized_refs = {}, Counter(), 0
    with (source / 'redacted/messages.jsonl').open('rb') as handle:
        for line in handle:
            row = metadata_object(line)
            if row['message_id'] in train_ids:
                ordinal = row['source_record_index']
                if ordinal in retained:
                    raise TopicContractError('duplicate retained source ordinal')
                retained[ordinal] = row
                normalized_kinds[row['message_kind']] += 1
                normalized_refs += bool(row.get('reply_to'))
    kinds, quote_kinds, retained_kinds = Counter(), Counter(), Counter()
    id_namespace, server_namespace, references = set(), set(), []
    total = matched = retained_refs = 0
    for ordinal, row in export_metadata(raw):
        total += 1
        kind = str(row.get('renderType') or 'text')
        kinds[kind] += 1
        quote = row.get('quoteServerId')
        if quote:
            quote_kinds[kind] += 1
            references.append(str(quote))
        for field, namespace in (('id', id_namespace), ('serverId', server_namespace)):
            if row.get(field) is not None:
                namespace.add(str(row[field]))
        if ordinal in retained:
            # Checks importer identity and zero-based ordinal without printing IDs.
            if str(row.get('id')) != retained[ordinal]['message_id']:
                raise TopicContractError('raw and normalized message identity disagree')
            matched += 1
            retained_kinds[kind] += 1
            retained_refs += bool(quote)
    if total != manifest['message_count'] or matched != len(retained):
        raise TopicContractError('quote audit coverage mismatch')
    scope = read_json(consent)['scope']
    allowed = set(scope['message_types']) | set(scope['media_types'])
    return {'schema_version': 'topic-quote-provenance-v1', 'raw_messages': total,
            'raw_render_type_counts': dict(kinds), 'raw_quote_reference_counts': dict(quote_kinds),
            'quote_reference_count': len(references),
            'quote_reference_matches_id': sum(ref in id_namespace for ref in references),
            'quote_reference_matches_server_id': sum(ref in server_namespace for ref in references),
            'quote_reference_matches_neither': sum(ref not in id_namespace and ref not in server_namespace
                                                  for ref in references),
            'quote_rows_outside_message_type_scope': sum(n for kind, n in quote_kinds.items() if kind not in allowed),
            'retained_train_messages': matched, 'retained_train_raw_type_counts': dict(retained_kinds),
            'retained_train_normalized_kind_counts': dict(normalized_kinds),
            'retained_train_raw_quote_references': retained_refs,
            'retained_train_normalized_reply_to': normalized_refs,
            'consent_message_types': sorted(allowed), 'message_content_decoded': False,
            'final_test_body_decoded': False, 'consent_modified': False,
            'quote_content_imported': False, 'raw_id_values_published': False}
