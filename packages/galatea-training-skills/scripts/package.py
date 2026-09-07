#!/usr/bin/env python3
"""Build a deterministic, self-validating skill bundle archive."""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import sys
import tarfile
import tempfile
from pathlib import Path


def load_validator(root):
    spec = importlib.util.spec_from_file_location("bundle_validate", root / "scripts" / "validate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def included_files(root):
    return [path for path in sorted(root.rglob("*")) if path.is_file() and not path.is_symlink()
            and path.name != "MANIFEST.json" and path.suffix != ".pyc"
            and not any(part in {"__pycache__", "dist", ".git"} for part in path.parts)]


def build(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    validator = load_validator(root)
    validator.validate_package(root)
    manifest = {
        "format_version": "galatea.skill-bundle/v1",
        "lock": json.loads((root / "skill-lock.json").read_text()),
        "files": [{"path": str(path.relative_to(root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                  for path in included_files(root)],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        tar_path = Path(directory) / "bundle.tar"
        with tarfile.open(tar_path, "w", format=tarfile.PAX_FORMAT) as archive:
            for path in included_files(root):
                info = archive.gettarinfo(str(path), f"galatea-training-skills/{path.relative_to(root)}")
                info.mtime = 0; info.uid = 0; info.gid = 0; info.uname = ""; info.gname = ""; info.mode = 0o444
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
            data = json.dumps(manifest, sort_keys=True, indent=2).encode() + b"\n"
            info = tarfile.TarInfo("galatea-training-skills/MANIFEST.json")
            info.size = len(data); info.mtime = 0; info.mode = 0o444
            archive.addfile(info, __import__("io").BytesIO(data))
        with tar_path.open("rb") as source, output.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
                compressed.write(source.read())
    return output


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "dist" / "galatea-training-skills.tar.gz"
    print(build(root, destination))
