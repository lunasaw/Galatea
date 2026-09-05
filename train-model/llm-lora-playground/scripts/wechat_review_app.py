#!/usr/bin/env python3
"""Local, auto-saving review UI and provisional all-keep baseline exporter.

This tool operates on a private copy of the already-redacted review export.  It
never edits the source dataset and it never writes the governed ``datasets/``
directory.  The initial copy is deliberately marked ``baseline_only`` because
the default ``keep`` labels are not human review evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


SPLITS = ("train", "validation", "test")
STATUSES = ("keep", "redact_keep", "reject", "uncertain")
DEFAULT_SOURCE = Path(
    "/data/ai/chenzhangyue/code/data/data-deal/output/"
    "wechat_aa807aaad90dc4463964/review_exports/v1"
)
DEFAULT_RUNTIME = Path("platform-data/llm-private/wechat-review-baseline/wechat_aa807aaad90dc4464")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict) or not value.get("sample_id"):
                raise ValueError(f"invalid candidate at {path}:{line_number}")
            rows.append(value)
    return rows


def _baseline_row(row: dict[str, Any], split: str) -> dict[str, Any]:
    copied = json.loads(json.dumps(row, ensure_ascii=False))
    metadata = dict(copied.get("metadata", {}))
    metadata.update(
        {
            "split": split,
            "review_status": "keep",
            "review_mode": "baseline_default_keep",
            "baseline_only": True,
            "human_review_completed": False,
            "formal_training_eligible": False,
        }
    )
    copied["metadata"] = metadata
    return copied


def export_baseline(runtime_root: Path, rows_by_split: dict[str, list[dict[str, Any]]], source_manifest: dict[str, Any]) -> dict[str, Any]:
    baseline_root = runtime_root / "baseline"
    datasets_root = baseline_root / "datasets"
    datasets_root.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    for split in SPLITS:
        output = datasets_root / f"{split}.jsonl"
        with output.open("w", encoding="utf-8") as handle:
            for row in rows_by_split[split]:
                handle.write(json.dumps(_baseline_row(row, split), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        counts[split] = len(rows_by_split[split])
        digests[split] = sha256_file(output)
    manifest = {
        "schema_version": "wechat-baseline-copy-v1",
        "baseline_only": True,
        "formal_training_eligible": False,
        "human_review_completed": False,
        "review_status_policy": "all_rows_default_keep_for_baseline_only",
        "dataset_id": source_manifest.get("dataset_id"),
        "source_sha256": source_manifest.get("source_sha256"),
        "source_review_export": "review_exports/v1",
        "split_counts": counts,
        "split_file_sha256": digests,
        "generated_at": utc_now(),
        "note": "Copied redacted candidates with provisional default keep labels. Never use as governed SFT data.",
    }
    atomic_write_json(baseline_root / "baseline_manifest.json", manifest)
    (baseline_root / "README.md").write_text(
        "# Provisional baseline copy\n\n"
        "This copy is generated for UI and pipeline smoke testing. Every row starts as "
        "`keep`, but no human review or consent verification is claimed. It is not eligible "
        "for formal training and must not replace the source dataset.\n",
        encoding="utf-8",
    )
    return manifest


def init_workspace(source_root: Path, runtime_root: Path, refresh: bool = False) -> dict[str, Any]:
    source_root = source_root.expanduser().resolve()
    runtime_root = runtime_root.expanduser().resolve()
    required = [source_root / "review_manifest.json", *(source_root / f"{split}_candidates.jsonl" for split in SPLITS)]
    missing = [str(path) for path in required if not path.is_file() or path.is_symlink()]
    if missing:
        raise FileNotFoundError("missing review export files: " + ", ".join(missing))
    if runtime_root.exists() and not refresh:
        state_path = runtime_root / "review_state.json"
        if state_path.is_file():
            return read_json(state_path)
        raise FileExistsError(f"runtime root exists without state: {runtime_root}")
    if runtime_root.exists() and refresh:
        backup = runtime_root.with_name(runtime_root.name + ".previous-" + datetime.now().strftime("%Y%m%dT%H%M%S"))
        os.replace(runtime_root, backup)
    runtime_root.mkdir(parents=True, exist_ok=False)
    candidates_root = runtime_root / "candidates"
    candidates_root.mkdir()
    rows_by_split: dict[str, list[dict[str, Any]]] = {}
    all_rows: list[dict[str, Any]] = []
    for split in SPLITS:
        source_file = source_root / f"{split}_candidates.jsonl"
        target_file = candidates_root / source_file.name
        shutil.copy2(source_file, target_file)
        rows_by_split[split] = read_rows(target_file)
        all_rows.extend(rows_by_split[split])
    source_manifest = read_json(source_root / "review_manifest.json")
    decisions = {
        row["sample_id"]: {
            "review_status": "keep",
            "reason": "baseline_default_keep",
            "review_mode": "baseline_default_keep",
            "baseline_only": True,
            "human_review_completed": False,
            "reviewed_at": None,
        }
        for row in all_rows
    }
    counts = Counter(item["review_status"] for item in decisions.values())
    state = {
        "schema_version": "wechat-review-state-v1",
        "dataset_id": source_manifest.get("dataset_id"),
        "source_review_manifest_sha256": sha256_file(source_root / "review_manifest.json"),
        "runtime_root": str(runtime_root),
        "candidate_count": len(all_rows),
        "default_status": "keep",
        "baseline_only": True,
        "human_review_completed": False,
        "formal_training_eligible": False,
        "updated_at": utc_now(),
        "status_counts": dict(sorted(counts.items())),
        "decisions": decisions,
    }
    atomic_write_json(runtime_root / "review_state.json", state)
    export_baseline(runtime_root, rows_by_split, source_manifest)
    atomic_write_json(runtime_root / "source_review_manifest.json", source_manifest)
    (runtime_root / "README.md").write_text(
        "# WeChat local review workspace\n\n"
        "This is a private copy of the redacted review export. The initial UI selection is "
        "`keep` for baseline smoke only. Every change is auto-saved to `review_state.json`; "
        "the source export and governed dataset remain untouched.\n",
        encoding="utf-8",
    )
    return state


def load_workspace(runtime_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
    runtime_root = runtime_root.expanduser().resolve()
    state = read_json(runtime_root / "review_state.json")
    rows: list[dict[str, Any]] = []
    split_by_index: dict[str, int] = {}
    for split in SPLITS:
        for row in read_rows(runtime_root / "candidates" / f"{split}_candidates.jsonl"):
            split_by_index[row["sample_id"]] = len(rows)
            row["_split"] = split
            rows.append(row)
    rows.sort(key=lambda row: row["sample_id"])
    return state, rows, split_by_index


def update_state(runtime_root: Path, sample_id: str, status: str, reason: str = "", reviewer_id: str = "local") -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"review_status must be one of {STATUSES}")
    path = runtime_root / "review_state.json"
    state = read_json(path)
    decisions = state.setdefault("decisions", {})
    if sample_id not in decisions:
        raise KeyError(f"unknown sample_id: {sample_id}")
    decisions[sample_id] = {
        "review_status": status,
        "reason": reason[:500],
        "review_mode": "local_manual_review",
        "baseline_only": False,
        "human_review_completed": True,
        "reviewer_id": reviewer_id[:120],
        "reviewed_at": utc_now(),
    }
    state["updated_at"] = utc_now()
    state["status_counts"] = dict(sorted(Counter(item["review_status"] for item in decisions.values()).items()))
    atomic_write_json(path, state)
    # Keep a compact append-only audit trail in addition to the latest state.
    audit_path = runtime_root / "review_events.jsonl"
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"sample_id": sample_id, **decisions[sample_id]}, ensure_ascii=False, sort_keys=True) + "\n")
    return state


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WeChat 脱敏数据审核</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; background: #f4f6f8; color: #17202a; }
    header { background: #17324d; color: white; padding: 18px 24px; position: sticky; top: 0; z-index: 2; }
    header h1 { margin: 0 0 6px; font-size: 21px; }
    header p { margin: 0; color: #d8e6f2; font-size: 13px; }
    main { max-width: 1100px; margin: 22px auto; padding: 0 16px 60px; }
    .notice { background: #fff7df; border: 1px solid #e9ca68; border-radius: 10px; padding: 12px 14px; margin-bottom: 16px; }
    .toolbar, .card { background: white; border: 1px solid #d9e0e7; border-radius: 10px; box-shadow: 0 2px 7px #102a4314; }
    .toolbar { padding: 12px 14px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .toolbar input { width: 90px; padding: 7px; }
    button { border: 1px solid #9eabb8; border-radius: 7px; background: white; padding: 8px 12px; cursor: pointer; }
    button:hover { background: #eef4f8; }
    .status-counts { margin-left: auto; font-size: 13px; color: #52606d; }
    .card { margin-top: 16px; padding: 18px; }
    .meta { display: flex; gap: 12px; flex-wrap: wrap; font-size: 13px; color: #52606d; border-bottom: 1px solid #edf0f2; padding-bottom: 12px; }
    .message { margin: 14px 0; padding: 12px 14px; border-radius: 9px; white-space: pre-wrap; line-height: 1.55; }
    .message.system { background: #f0f3f5; color: #52606d; }
    .message.user { background: #e9f3ff; border-left: 4px solid #3b82c4; }
    .message.assistant { background: #edfaef; border-left: 4px solid #37a65a; }
    .role { display: block; font-weight: 700; font-size: 12px; margin-bottom: 5px; color: #52606d; }
    .decision { margin-top: 18px; padding-top: 14px; border-top: 1px solid #edf0f2; }
    .decision label { display: inline-flex; gap: 6px; align-items: center; margin: 5px 16px 5px 0; }
    textarea { width: 100%; min-height: 62px; box-sizing: border-box; margin-top: 8px; padding: 9px; border: 1px solid #b8c2cc; border-radius: 7px; font: inherit; }
    .hint { color: #68737d; font-size: 12px; margin-top: 8px; }
    .saved { color: #16733b; font-size: 13px; min-height: 18px; }
    .danger { color: #a33a2b; }
  </style>
</head>
<body>
<header><h1>WeChat 脱敏数据本地审核</h1><p>页面只读取本机脱敏副本；选择会自动保存，不会修改原始导出。</p></header>
<main>
  <div class="notice"><strong>当前是 baseline 副本：</strong>首次复制时全部预选为 <code>keep</code>，仅用于先跑通页面和数据基准；这不代表人工审核完成，也不能直接作为正式训练数据。</div>
  <div class="toolbar">
    <button id="prev">上一条</button><button id="next">下一条</button>
    <label>跳转 <input id="jump" type="number" min="1"> / <span id="total">-</span></label>
    <button id="go">前往</button>
    <span class="status-counts" id="counts">加载中…</span>
  </div>
  <section class="card">
    <div class="meta"><span id="indexLabel"></span><span id="sampleId"></span><span id="split"></span><span id="score"></span></div>
    <div id="messages"></div>
    <div class="decision">
      <strong>审核标签</strong><br>
      <label><input type="radio" name="decision" value="keep"> keep：可以保留</label>
      <label><input type="radio" name="decision" value="redact_keep"> redact_keep：修改脱敏后保留</label>
      <label><input type="radio" name="decision" value="reject"> reject：排除</label>
      <label><input type="radio" name="decision" value="uncertain"> uncertain：暂不决定</label>
      <textarea id="reason" placeholder="可选：说明隐私、第三方、上下文、重复或质量问题"></textarea>
      <div class="hint">快捷键：1 keep，2 redact_keep，3 reject，4 uncertain；选择后会自动保存并进入下一条。</div>
      <div class="saved" id="saved"></div>
    </div>
  </section>
</main>
<script>
let rows = [], index = 0, state = null, busy = false;
const $ = id => document.getElementById(id);
const basePath = window.location.pathname.endsWith('/') ? window.location.pathname : window.location.pathname + '/';
const apiPath = name => basePath + 'api/' + name;
async function getJSON(url, options) { const r = await fetch(url, options); if (!r.ok) throw new Error(await r.text()); return r.json(); }
async function load() { const data = await getJSON(apiPath('data')); rows = data.rows; state = data.state; $('total').textContent = rows.length; render(); }
function render() {
  if (!rows.length) return;
  const row = rows[index]; const decision = state.decisions[row.sample_id] || {review_status: 'keep', reason: ''};
  $('indexLabel').textContent = `第 ${index + 1} / ${rows.length} 条`;
  $('sampleId').textContent = `sample_id: ${row.sample_id}`;
  $('split').textContent = `split: ${row._split}`;
  $('score').textContent = `机器分数: ${row.machine_score ?? '-'}`;
  $('messages').innerHTML = row.messages.map(m => `<div class="message ${m.role}"><span class="role">${m.role}</span>${escapeHTML(m.content)}</div>`).join('');
  document.querySelectorAll('input[name=decision]').forEach(r => r.checked = r.value === decision.review_status);
  $('reason').value = decision.reason || '';
  $('saved').textContent = decision.review_mode === 'baseline_default_keep' ? '当前为 baseline 默认 keep，尚未人工确认。' : `已自动保存：${decision.review_status}`;
  updateCounts(); $('jump').value = index + 1;
}
function escapeHTML(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function updateCounts() { const c = state.status_counts || {}; $('counts').textContent = `keep ${c.keep||0} · redact_keep ${c.redact_keep||0} · reject ${c.reject||0} · uncertain ${c.uncertain||0}`; }
async function choose(status) {
  if (busy || !rows.length) return; busy = true;
  try { state = await getJSON(apiPath('review'), {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({sample_id: rows[index].sample_id, review_status: status, reason: $('reason').value})}); $('saved').textContent = `已自动保存：${status}`; updateCounts(); setTimeout(() => { if (index < rows.length - 1) { index++; render(); } }, 250); }
  catch (e) { $('saved').textContent = '保存失败：' + e.message; $('saved').className = 'saved danger'; }
  finally { busy = false; }
}
document.querySelectorAll('input[name=decision]').forEach(r => r.addEventListener('change', e => choose(e.target.value)));
$('prev').onclick = () => { if (index > 0) { index--; render(); } };
$('next').onclick = () => { if (index < rows.length - 1) { index++; render(); } };
$('go').onclick = () => { const n = Math.max(1, Math.min(rows.length, Number($('jump').value||1))); index = n - 1; render(); };
document.addEventListener('keydown', e => { if (['INPUT','TEXTAREA'].includes(document.activeElement.tagName)) return; const map = {'1':'keep','2':'redact_keep','3':'reject','4':'uncertain'}; if (map[e.key]) choose(map[e.key]); });
load().catch(e => $('saved').textContent = '加载失败：' + e.message);
</script>
</body></html>"""


class ReviewHandler(BaseHTTPRequestHandler):
    runtime_root: Path
    rows: list[dict[str, Any]]
    state: dict[str, Any]

    def _send_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        # Reverse proxies commonly preserve a path prefix (for example
        # ``/GC5026/absproxy/51644/``). Treat any prefix-root URL as the app
        # shell while keeping the API suffix routes below distinct.
        if path in {"", "/", "/index.html"} or path.endswith("/") or path.endswith("/index.html"):
            payload = HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if path == "/api/data" or path.endswith("/api/data"):
            self.state = read_json(self.runtime_root / "review_state.json")
            self._send_json({"state": self.state, "rows": self.rows})
            return
        if path == "/api/health" or path.endswith("/api/health"):
            self._send_json({"status": "ok", "dataset_id": self.state.get("dataset_id"), "baseline_only": True})
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/review" and not path.endswith("/api/review"):
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            body = self._read_body()
            self.state = update_state(
                self.runtime_root,
                str(body.get("sample_id", "")),
                str(body.get("review_status", "")),
                str(body.get("reason", "")),
                str(body.get("reviewer_id", "local")),
            )
            self._send_json(self.state)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[review] {self.address_string()} - {format % args}")


def serve(runtime_root: Path, host: str, port: int) -> None:
    state, rows, _ = load_workspace(runtime_root)
    handler = type("BoundReviewHandler", (ReviewHandler,), {"runtime_root": runtime_root.resolve(), "rows": rows, "state": state})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Review UI: http://{host}:{port}/")
    print(f"Workspace: {runtime_root.resolve()}")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "serve"))
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.command == "init":
        state = init_workspace(args.source_root, args.runtime_root, args.refresh)
        print(json.dumps({"status": "ok", "runtime_root": state["runtime_root"], "candidate_count": state["candidate_count"], "status_counts": state["status_counts"], "baseline_only": True}, ensure_ascii=False, indent=2))
        return 0
    serve(args.runtime_root.expanduser().resolve(), args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
