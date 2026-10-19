"""Serve MCP HTML with an opaque origin and a resource-specific CSP."""
from __future__ import annotations

import base64
import re
from urllib.parse import urlsplit


def resource_document(result: dict, uri: str, host: str) -> tuple[bytes, str]:
    resource = next((item for item in result.get('contents', [])
                     if isinstance(item, dict) and item.get('uri') == uri
                     and str(item.get('mimeType', '')).split(';')[0].strip() == 'text/html'), None)
    if resource is None:
        raise ValueError('MCP resource did not contain the requested HTML document')
    html = resource.get('text')
    body = html.encode() if isinstance(html, str) else base64.b64decode(resource.get('blob', ''), validate=True)
    if not body or len(body) > 8 * 1024 * 1024:
        raise ValueError('MCP application document is empty or too large')
    def mapping(value):
        return value if isinstance(value, dict) else {}

    csp = mapping(mapping(mapping(resource.get('_meta')).get('ui')).get('csp'))

    def domains(key, schemes):
        values = []
        candidates = csp.get(key, [])
        for value in candidates if isinstance(candidates, list) else []:
            # Accept origins only. In particular, metadata cannot inject CSP
            # keywords, a new directive, or the console's own network origin.
            if not isinstance(value, str) or not re.fullmatch(r'[a-z]+://(?:\*\.)?[A-Za-z0-9.-]+(?::[0-9]+)?', value):
                continue
            parsed = urlsplit(value)
            try:
                host_name = urlsplit('http://' + host).hostname or ''
                candidate = parsed.hostname or ''
                # Reject this Host even with default-port spelling or wildcard
                # metadata. App network access must use its external service.
                same_host = candidate.lower() == host_name.lower() or (
                    candidate.startswith('*.') and host_name.lower().endswith(candidate[1:].lower()))
                valid_port = parsed.port is None or 0 < parsed.port <= 65535
            except ValueError:
                continue
            if parsed.scheme not in schemes or same_host or not valid_port:
                continue
            values.append(value)
        return ' '.join(values)

    resources = domains('resourceDomains', {'https', 'http'})
    connections = domains('connectDomains', {'https', 'http', 'wss', 'ws'})
    frames = domains('frameDomains', {'https', 'http'})
    bases = domains('baseUriDomains', {'https', 'http'})
    policy = ("default-src 'none'; sandbox allow-scripts; frame-ancestors 'self'; form-action 'none'; "
              f"base-uri {bases or chr(39) + 'none' + chr(39)}; "
              f"script-src 'unsafe-inline' {resources}; style-src 'unsafe-inline' {resources}; "
              f"img-src data: blob: {resources}; font-src data: {resources}; media-src data: blob: {resources}; "
              f"connect-src {connections or chr(39) + 'none' + chr(39)}; frame-src {frames or chr(39) + 'none' + chr(39)}")
    return body, policy
