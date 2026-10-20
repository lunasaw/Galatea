"""Render local, self-contained HTML without giving it the console's origin."""
import base64
from pathlib import PurePosixPath


PREVIEW_CSP = (
    "default-src 'none'; sandbox allow-scripts; frame-ancestors 'self'; "
    "script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; font-src data:; media-src data: blob:; "
    "connect-src 'none'; frame-src 'none'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'"
)


async def read_html_preview(native, path, request_id):
    if (not isinstance(path, str) or not path.startswith('/') or path.startswith('//')
            or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or PurePosixPath(path).suffix.lower() not in {'.html', '.htm'}):
        raise ValueError('HTML preview requires an absolute .html or .htm path')
    # Keep the same native filesystem permissions as the source viewer.
    result = await native.call('fs/readFile', {'path': path}, request_id)
    encoded = result.get('dataBase64')
    if not isinstance(encoded, str) or len(encoded) > 12 * 1024 * 1024:
        raise ValueError('HTML preview is unavailable or too large')
    body = base64.b64decode(encoded, validate=True)
    if len(body) > 8 * 1024 * 1024:
        raise ValueError('HTML preview is too large')
    return body
