#!/usr/bin/env python3
"""Validate a released Galatea training skill bundle using only the standard library."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


SKILL_NAMES = {
    "training-campaign", "dataset-readiness", "model-strategy",
    "experiment-design", "mlflow-analysis", "model-delivery",
}
LINK = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SECRET = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:token|password|secret)\s*[:=]\s*[^\s${}]{12,})", re.I)
ABSOLUTE_DEVELOPER_PATH = re.compile(r"(?:/Users/[^/\s]+/|/home/[^/\s]+/|[A-Za-z]:\\Users\\)")


class ValidationError(ValueError):
    pass


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def parse_frontmatter(path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValidationError(f"invalid frontmatter: {path}")
    block = text[4:].split("\n---\n", 1)[0]
    fields = {}
    for line in block.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip("'\"")
    if set(fields) != {"name", "description"} or not fields["description"]:
        raise ValidationError(f"frontmatter requires only name and description: {path}")
    if not NAME.fullmatch(fields["name"]):
        raise ValidationError(f"invalid skill name: {fields['name']}")
    return fields, text


def package_files(root):
    for path in sorted(root.rglob("*")):
        if any(part in {"__pycache__", ".git", "dist"} for part in path.parts):
            continue
        if path.is_symlink():
            raise ValidationError(f"symlink is forbidden: {path.relative_to(root)}")
        if path.is_file() and path.name != "MANIFEST.json" and path.suffix != ".pyc":
            yield path


def validate_links(root, path, text):
    for raw in LINK.findall(text):
        target = raw.split("#", 1)[0]
        if not target or "://" in target or target.startswith("#"):
            continue
        candidate = (path.parent / target).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as error:
            raise ValidationError(f"relative link escapes package: {path}: {raw}") from error
        if not candidate.is_file():
            raise ValidationError(f"broken relative link: {path}: {raw}")


def validate_manifest(root):
    manifest_path = root / "MANIFEST.json"
    if not manifest_path.is_file():
        raise ValidationError("MANIFEST.json is required")
    manifest = json.loads(manifest_path.read_text())
    actual = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in package_files(root)}
    expected = {item["path"]: item["sha256"] for item in manifest["files"]}
    if actual != expected:
        raise ValidationError("manifest digest mismatch")
    if manifest["lock"] != json.loads((root / "skill-lock.json").read_text()):
        raise ValidationError("manifest lock mismatch")


def validate_package(root, verify_manifest=False):
    root = Path(root).resolve()
    # Reject links before reading content so a later validation error cannot mask them.
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValidationError(f"symlink is forbidden: {path.relative_to(root)}")
    required = [root / "contracts" / "tools.json", root / "contracts" / "agent-turn.schema.json", root / "skill-lock.json"]
    if any(not path.is_file() for path in required):
        raise ValidationError("required contract or lock file is missing")
    names = []
    for skill_dir in sorted((root / "skills").iterdir()):
        if not skill_dir.is_dir():
            continue
        fields, text = parse_frontmatter(skill_dir / "SKILL.md")
        names.append(fields["name"])
        validate_links(root, skill_dir / "SKILL.md", text)
    if len(names) != len(set(names)):
        raise ValidationError("duplicate skill name")
    for skill_dir in sorted((root / "skills").iterdir()):
        if skill_dir.is_dir() and parse_frontmatter(skill_dir / "SKILL.md")[0]["name"] != skill_dir.name:
            raise ValidationError(f"skill name does not match directory: {skill_dir}")
    if set(names) != SKILL_NAMES:
        raise ValidationError(f"expected exactly six skills, got {sorted(names)}")
    scenarios = list((root / "tests" / "scenarios").glob("*.json"))
    if len(scenarios) != 10:
        raise ValidationError("expected exactly ten frozen scenarios")
    tools_contract = json.loads(required[0].read_text())
    lock = json.loads(required[2].read_text())
    if digest(tools_contract["tools"]) != lock.get("tools_sha256"):
        raise ValidationError("tool contract digest does not match lock")
    if tools_contract.get("protocol_version") != "galatea.tools/v1" or len(tools_contract.get("tools", [])) != 17:
        raise ValidationError("invalid tools contract")
    if digest(json.loads(required[1].read_text())) != lock.get("agent_turn_schema_sha256"):
        raise ValidationError("agent turn schema digest does not match lock")
    for path in package_files(root):
        if path.suffix.lower() not in {".md", ".json", ".py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".md":
            validate_links(root, path, text)
        scanner_fixture = path == root / "scripts" / "validate.py" or path == root / "tests" / "test_validate.py"
        if not scanner_fixture and SECRET.search(text):
            raise ValidationError(f"possible secret: {path.relative_to(root)}")
        if not scanner_fixture and ABSOLUTE_DEVELOPER_PATH.search(text):
            raise ValidationError(f"developer absolute path: {path.relative_to(root)}")
    if verify_manifest:
        validate_manifest(root)
    return {"skills": len(names), "scenarios": len(scenarios), "tools": len(tools_contract["tools"])}


if __name__ == "__main__":
    package_root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    try:
        print(json.dumps(validate_package(package_root), sort_keys=True))
    except (ValidationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"validation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
