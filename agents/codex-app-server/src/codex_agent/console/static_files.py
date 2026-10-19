"""Serve only public build assets, with compression and content-aware caching."""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import re
from functools import lru_cache
from pathlib import Path


CSP = b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob: https:; media-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'"
_ROOT_FILES = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript'),
    '/transport.js': ('transport.js', 'text/javascript'),
    '/audio-capture-worklet.js': ('audio-capture-worklet.js', 'text/javascript'),
    '/style.css': ('style.css', 'text/css'),
    '/THIRD_PARTY_NOTICES.txt': ('THIRD_PARTY_NOTICES.txt', 'text/plain; charset=utf-8'),
}


def accepts_gzip(value):
    qualities = {}
    for item in value.lower().split(','):
        name, *parameters = item.strip().split(';')
        quality = 1.0
        for parameter in parameters:
            if parameter.strip().startswith('q='):
                try:
                    quality = float(parameter.strip()[2:])
                except ValueError:
                    quality = 0.0
        qualities[name] = quality
    return qualities.get('gzip', qualities.get('*', 0)) > 0


@lru_cache(maxsize=128)
def representation(path, modified, size, compressed):
    # The stat signature invalidates cached bytes during a source deployment.
    body = path.read_bytes()
    if compressed:
        body = gzip.compress(body, compresslevel=6, mtime=0)
    return body, ('"' + hashlib.sha256(body).hexdigest() + '"').encode()


class StaticFiles:
    def __init__(self, directory):
        self.directory = Path(directory)

    async def serve(self, scope, send):
        path = scope.get('path', '/')
        immutable = False
        if path in _ROOT_FILES:
            filename, content_type = _ROOT_FILES[path]
        elif re.fullmatch(r'/assets/[A-Za-z0-9_-]+-[A-Za-z0-9_-]{8,64}\.(js|css)', path):
            filename = path[1:]
            content_type = 'text/javascript' if path.endswith('.js') else 'text/css'
            immutable = True
        elif re.fullmatch(r'/math-fonts/KaTeX_[A-Za-z0-9_.-]+\.woff2', path):
            filename, content_type = path[1:], 'font/woff2'
        else:
            return False
        file = self.directory / filename
        try:
            stat = file.stat()
        except (FileNotFoundError, NotADirectoryError):
            return False
        headers = dict(scope.get('headers', []))
        compressible = content_type.startswith('text/')
        compressed = compressible and accepts_gzip(headers.get(b'accept-encoding', b'').decode('latin1'))
        body, etag = await asyncio.to_thread(representation, file, stat.st_mtime_ns, stat.st_size, compressed)
        response_headers = [
            (b'content-type', content_type.encode()),
            (b'cache-control', b'public, max-age=31536000, immutable' if immutable else b'no-cache'),
            (b'etag', etag),
            (b'x-content-type-options', b'nosniff'),
            (b'content-security-policy', CSP),
        ]
        if compressible:
            response_headers.append((b'vary', b'Accept-Encoding'))
        if compressed:
            response_headers.append((b'content-encoding', b'gzip'))
        candidates = headers.get(b'if-none-match', b'').split(b',')
        unchanged = any(tag.strip().removeprefix(b'W/') in (etag, b'*') for tag in candidates)
        if not unchanged:
            response_headers.append((b'content-length', str(len(body)).encode()))
        await send({'type': 'http.response.start', 'status': 304 if unchanged else 200, 'headers': response_headers})
        await send({'type': 'http.response.body', 'body': b'' if unchanged or scope.get('method') == 'HEAD' else body})
        return True
