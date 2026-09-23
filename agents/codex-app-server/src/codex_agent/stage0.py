"""Evidence gates: absence of proof is never a passing deployment check."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import stat

from .catalog import digest


class GateError(RuntimeError):
    """A required release assertion was not established."""


REQUIRED_CHECKS = frozenset({
    "source_provenance", "runtime_package", "protocol_schema", "catalog", "effective_config",
    "experimental_positive", "experimental_negative", "full_new_surface", "subset_new_surface",
    "full_resume_surface", "subset_resume_surface", "dynamic_round_trip", "dynamic_error_round_trip",
    "intent_crash_windows", "reconciler_freshness", "http_auth_csrf", "secret_boundary",
})


def expected_model_tools(dynamic_tools: list[dict]) -> list[dict]:
    expected = copy.deepcopy(dynamic_tools)
    for namespace in expected:
        for tool in namespace["tools"]:
            tool["parameters"] = tool.pop("inputSchema")
            tool["strict"] = False
    return expected


def tool_surface_differences(request: dict, dynamic_tools: list[dict]) -> list[dict]:
    """JSON-pointer differences; preserve evidence of lost validation keywords."""
    differences = []
    missing = object()

    def visit(expected, observed, path):
        if isinstance(expected, dict) and isinstance(observed, dict):
            for key in sorted(expected.keys() | observed.keys()):
                visit(expected.get(key, missing), observed.get(key, missing),
                      path + "/" + key.replace("~", "~0").replace("/", "~1"))
        elif isinstance(expected, list) and isinstance(observed, list) and len(expected) == len(observed):
            for index, (left, right) in enumerate(zip(expected, observed)):
                visit(left, right, f"{path}/{index}")
        elif expected != observed:
            differences.append({"path": path,
                                "expected": {"absent": True} if expected is missing else expected,
                                "observed": {"absent": True} if observed is missing else observed})

    visit(expected_model_tools(dynamic_tools), request.get("tools") if isinstance(request, dict) else None,
          "/tools")
    return differences


def assert_tool_inventory(request: dict, dynamic_tools: list[dict]) -> None:
    def inventory(tools):
        if not isinstance(tools, list):
            raise GateError("tool-surface is malformed")
        entries = []
        for namespace in tools:
            if (not isinstance(namespace, dict) or namespace.get("type") != "namespace"
                    or not isinstance(namespace.get("name"), str)
                    or not isinstance(namespace.get("tools"), list) or not namespace["tools"]):
                raise GateError("tool-surface has an unexpected capability")
            entries.append((namespace["name"], ""))
            for tool in namespace["tools"]:
                if not isinstance(tool, dict) or tool.get("type") != "function" or not isinstance(tool.get("name"), str):
                    raise GateError("tool-surface has an unexpected tool")
                entries.append((namespace["name"], tool["name"]))
        return sorted(entries)

    if not isinstance(request, dict) or inventory(request.get("tools")) != inventory(dynamic_tools):
        raise GateError("tool-surface membership differs from the frozen principal allowlist")


def assert_tool_surface(request: dict, dynamic_tools: list[dict]) -> None:
    # Compare the entire specification, including namespaces and schemas, not a
    # set of names that could hide duplicate registrations or an extra capability.
    if tool_surface_differences(request, dynamic_tools):
        raise GateError("tool-surface differs from the frozen principal allowlist")


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def package_manifest(root: Path) -> dict:
    entries = []
    if not root.is_dir() or root.is_symlink():
        raise GateError("Package root must be a directory")
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        entry = {"path": path.relative_to(root).as_posix(), "executable": mode & 0o111}
        if stat.S_ISREG(mode):
            entry.update(type="file", sha256=file_sha256(path))
        elif stat.S_ISDIR(mode):
            entry.update(type="directory")
        else:
            raise GateError("Symlinks and special files are not accepted in runtime packages")
        entries.append(entry)
    return {"entries": entries, "sha256": digest(entries)}


def require_stage0(record: dict, *, evidence_root: Path | None = None) -> None:
    """Check an administrator-owned evidence index and referenced file integrity.

    This validates a supplied index; it does not authenticate its producer or
    replace the individual acceptance tests. Production startup requires this gate.
    """
    if not isinstance(record, dict) or not isinstance(record.get("checks"), dict):
        raise GateError("stage-0 evidence is absent or malformed")
    checks = record.get("checks", {})
    failures = sorted(name for name in REQUIRED_CHECKS
                      if not isinstance(checks.get(name), dict)
                      or checks.get(name, {}).get("status") != "passed"
                      or not checks.get(name, {}).get("evidence_path"))
    if record.get("status") != "passed" or failures:
        raise GateError("stage-0 failed: " + ", ".join(failures or ["release status"]))
    if evidence_root is None:
        raise GateError("stage-0 requires a bound evidence directory")
    evidence_root = evidence_root.resolve()
    for name in sorted(REQUIRED_CHECKS):
        check = checks[name]
        relative = check.get("evidence_path")
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise GateError("stage-0 evidence path escapes its root")
        artifact = evidence_root / relative
        if not artifact.resolve().is_relative_to(evidence_root) or artifact.is_symlink():
            raise GateError("stage-0 evidence path escapes its root")
        try:
            actual = file_sha256(artifact)
        except OSError as exc:
            raise GateError("stage-0 evidence is unavailable: " + name) from exc
        if actual != check.get("evidence_sha256"):
            raise GateError("stage-0 evidence digest mismatch: " + name)
