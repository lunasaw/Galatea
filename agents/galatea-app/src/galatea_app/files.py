"""Bounded code snapshots at execution boundaries; raw datasets and secrets excluded."""

from pathlib import Path
import difflib
import hashlib
from .common import canonical, uid, utc

SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".md",
    ".css",
    ".html",
    ".txt",
    ".csv",
}
EXCLUDED = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "platform-data",
    "datasets",
    "checkpoints",
    "secrets",
    ".codex",
}


def snapshot(root, max_files=200, max_bytes=1024 * 1024):
    files = {}
    total = 0
    coverage = "complete"
    # Bound traversal too; a large tree is explicitly incomplete rather than silently sampled.
    import os

    visited = 0
    for parent, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in EXCLUDED
            and not d.startswith(".")
            and not (Path(parent) / d).is_symlink()
        )
        for name in sorted(names):
            visited += 1
            if visited > 2000:
                return {"files": files, "coverage": "truncated", "observed_at": utc()}
            path = Path(parent) / name
            if (
                path.suffix not in SUFFIXES
                or path.is_symlink()
                or name.startswith(".")
                or any(
                    k in name.lower()
                    for k in (
                        "secret",
                        "credential",
                        "password",
                        "token",
                        "private-key",
                    )
                )
            ):
                continue
            if path.stat().st_size > 131072:
                coverage = "truncated"
                continue
            if len(files) >= max_files or total + path.stat().st_size > max_bytes:
                return {"files": files, "coverage": "truncated", "observed_at": utc()}
            try:
                data = path.read_bytes()
                text = data.decode("utf-8")
            except (OSError, UnicodeError):
                coverage = "incomplete"
                continue
            total += len(data)
            files[str(path.relative_to(root))] = text
    return {"files": files, "coverage": coverage, "observed_at": utc()}


def changes(before, after, stage_id, attempt_id):
    result = []
    for path in sorted(before["files"].keys() | after["files"].keys()):
        old = before["files"].get(path, "")
        new = after["files"].get(path, "")
        if old == new:
            continue
        result.append(
            {
                "change_id": uid("change"),
                "relative_path": path,
                "operation": (
                    "add"
                    if path not in before["files"]
                    else "delete" if path not in after["files"] else "modify"
                ),
                "base_digest": hashlib.sha256(old.encode()).hexdigest(),
                "current_digest": hashlib.sha256(new.encode()).hexdigest(),
                "stage_id": stage_id,
                "attempt_id": attempt_id,
                "hunks": list(
                    difflib.unified_diff(
                        old.splitlines(),
                        new.splitlines(),
                        fromfile="a/" + path,
                        tofile="b/" + path,
                        lineterm="",
                    )
                ),
                "checks": [],
                "observed_at": after["observed_at"],
                "source": "execution-boundary-snapshots",
                "coverage": (
                    "complete"
                    if before["coverage"] == after["coverage"] == "complete"
                    else "incomplete"
                ),
            }
        )
    return result
