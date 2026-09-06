"""Streaming importer protocol and path safety checks."""
from __future__ import annotations

import csv
import hashlib
import html.parser
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol


class ImportErrorSafe(ValueError):
    pass


class Importer(Protocol):
    extensions: tuple[str, ...]

    def iter_messages(self, path: Path, timezone: str) -> Iterator[dict[str, Any]]: ...


def _safe_path(path: Path, allowed_root: Path | None) -> Path:
    candidate = Path(path)
    if not candidate.exists():
        raise ImportErrorSafe("source does not exist")
    resolved = candidate.resolve(strict=True)
    if allowed_root is not None:
        root = Path(allowed_root).resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ImportErrorSafe("symlink_escape") from exc
    if not resolved.is_file():
        raise ImportErrorSafe("source is not a regular file")
    return resolved


class TextImporter:
    extensions = (".txt",)
    _line = re.compile(r"^\s*(?P<timestamp>\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)\s*(?:\t|\s+-\s+|\s+)(?P<speaker>[^:：]+)[:：]\s*(?P<text>.*)$")

    def iter_messages(self, path: Path, timezone: str) -> Iterator[dict[str, Any]]:
        with path.open(encoding="utf-8", errors="strict") as handle:
            for index, line in enumerate(handle):
                match = self._line.match(line.rstrip("\n"))
                if not match:
                    if line.strip():
                        raise ImportErrorSafe(f"text parse error at line {index + 1}")
                    continue
                yield {"source_record_index": index, **match.groupdict(), "kind": "text"}


class CsvImporter:
    extensions = (".csv",)

    def iter_messages(self, path: Path, timezone: str) -> Iterator[dict[str, Any]]:
        with path.open(newline="", encoding="utf-8", errors="strict") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ImportErrorSafe("csv missing header")
            for index, row in enumerate(reader):
                yield {"source_record_index": index, "timestamp": row.get("timestamp") or row.get("time"), "speaker": row.get("speaker") or row.get("sender"), "text": row.get("text") or row.get("content") or "", "kind": row.get("kind") or row.get("type") or "text"}


def _json_items(path: Path) -> Iterator[Any]:
    # A line-delimited JSON file is naturally streaming.  For a top-level
    # array we use ijson when present, otherwise a bounded decoder fallback.
    with path.open(encoding="utf-8", errors="strict") as handle:
        first = handle.read(1)
        handle.seek(0)
        if first == "{":
            # Stream the common {"messages": [...]} export without loading
            # the containing object or message array into memory.
            decoder = json.JSONDecoder(); buffer = ""; position = 0; started = False; eof = False
            marker = '"messages"'
            while not started:
                more = handle.read(1024 * 1024); buffer += more; eof = not more
                marker_pos = buffer.find(marker)
                if marker_pos >= 0:
                    array_start = buffer.find("[", marker_pos + len(marker))
                    if array_start >= 0:
                        buffer = buffer[array_start + 1:]; started = True; break
                if eof: raise ImportErrorSafe("json object missing messages array")
            while True:
                while position < len(buffer) and buffer[position] in " \t\r\n,": position += 1
                if position < len(buffer) and buffer[position] == "]": return
                if position < len(buffer):
                    try: item, end = decoder.raw_decode(buffer, position)
                    except json.JSONDecodeError: pass
                    else: yield item; position = end; continue
                more = handle.read(1024 * 1024)
                if not more: raise ImportErrorSafe("truncated json messages array")
                buffer = buffer[position:] + more; position = 0
        elif first == "[":
            decoder = json.JSONDecoder(); buffer = ""; position = 0; eof = False; started = False
            while True:
                if not started:
                    more = handle.read(1024 * 1024); buffer += more; eof = not more
                    left = buffer.find("[")
                    if left < 0:
                        if eof: raise ImportErrorSafe("json array missing")
                        continue
                    buffer = buffer[left + 1:]; started = True
                while True:
                    while position < len(buffer) and buffer[position] in " \t\r\n,": position += 1
                    if position >= len(buffer): break
                    if buffer[position] == "]": return
                    try: item, end = decoder.raw_decode(buffer, position)
                    except json.JSONDecodeError:
                        more = handle.read(1024 * 1024)
                        if not more: raise ImportErrorSafe("truncated json array")
                        buffer = buffer[position:] + more; position = 0; continue
                    yield item; position = end
                if eof: raise ImportErrorSafe("truncated json array")
                more = handle.read(1024 * 1024); buffer = buffer[position:] + more; position = 0; eof = not more
        else:
            for line_no, line in enumerate(handle, 1):
                if line.strip():
                    try: yield json.loads(line)
                    except json.JSONDecodeError as exc: raise ImportErrorSafe(f"json parse error at line {line_no}") from exc


class JsonImporter:
    extensions = (".json", ".jsonl")

    def iter_messages(self, path: Path, timezone: str) -> Iterator[dict[str, Any]]:
        for index, item in enumerate(_json_items(path)):
            if isinstance(item, dict) and isinstance(item.get("messages"), list):
                for nested in item["messages"]:
                    if not isinstance(nested, dict): raise ImportErrorSafe(f"json message {index} is not object")
                    yield {"source_record_index": index, **nested}
            elif isinstance(item, dict):
                yield {"source_record_index": index, **item}
            else: raise ImportErrorSafe(f"json message {index} is not object")


class _HtmlParser(html.parser.HTMLParser):
    def __init__(self): super().__init__(); self.rows: list[dict[str, str]] = []; self._current: dict[str, str] | None = None
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"div", "p", "li", "article", "message"} and (values.get("data-timestamp") or values.get("timestamp")):
            self._current = {"timestamp": values.get("data-timestamp") or values.get("timestamp") or "", "speaker": values.get("data-speaker") or values.get("speaker") or "", "kind": values.get("data-kind") or "text", "text": ""}
    def handle_data(self, data: str) -> None:
        if self._current is not None: self._current["text"] += data
    def handle_endtag(self, tag: str) -> None:
        if self._current is not None and tag in {"div", "p", "li", "article", "message"}:
            self.rows.append(self._current); self._current = None


class HtmlImporter:
    extensions = (".html", ".htm")
    def iter_messages(self, path: Path, timezone: str) -> Iterator[dict[str, Any]]:
        parser = _HtmlParser();
        # HTML exports are typically bounded; parser still avoids DOM loading.
        with path.open(encoding="utf-8", errors="strict") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), ""): parser.feed(chunk)
        for index, row in enumerate(parser.rows): yield {"source_record_index": index, **row}


IMPORTERS = {".txt": TextImporter(), ".csv": CsvImporter(), ".json": JsonImporter(), ".jsonl": JsonImporter(), ".html": HtmlImporter(), ".htm": HtmlImporter()}


def detect_importer(path: Path, *, allowed_root: Path | None = None) -> Importer:
    safe = _safe_path(path, allowed_root)
    try: return IMPORTERS[safe.suffix.casefold()]
    except KeyError as exc: raise ImportErrorSafe(f"unsupported format: {safe.suffix}") from exc


def import_messages(path: Path, *, timezone_name: str = "UTC", allowed_root: Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    safe = _safe_path(path, allowed_root); importer = detect_importer(safe, allowed_root=allowed_root)
    messages = list(importer.iter_messages(safe, timezone_name))
    digest = hashlib.sha256()
    with safe.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""): digest.update(chunk)
    return messages, {"format": safe.suffix.lstrip("."), "source_sha256": digest.hexdigest(), "source_size_bytes": safe.stat().st_size, "message_count": len(messages), "importer_version": "wechat-importers-v1", "source_ref": "conversations/messages"}
