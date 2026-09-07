#!/usr/bin/env python3
"""Build and test independent releases offline, outside the source checkout.

Prepare a wheelhouse with the dependencies declared by the MCP and Runner first.
This command performs no model calls, training, deployment, or dependency downloads.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "mcp": ("services/galatea-mcp", "galatea_mcp", "galatea-mcp"),
    "runner": ("agents/training-agent-runtime", "training_agent", "training-agent"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="New directory outside checkout")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("Python 3.11 or newer is required")
    output, wheelhouse = args.output.resolve(), args.wheelhouse.resolve(strict=True)
    if output == ROOT or ROOT in output.parents:
        parser.error("Output must be outside the repository")
    output.mkdir(parents=True, exist_ok=False)
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"PYTHONPATH", "PYTHONHOME", "CODEX_SDK_SOURCE"}
                   and not key.startswith("PIP_")}
    environment.update(PYTHONNOUSERSITE="1", PIP_CONFIG_FILE=os.devnull,
                       PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    report = {"schema_version": "galatea.local-installation/v1", "status": "running",
              "started_at": datetime.now(timezone.utc).isoformat(),
              "python": sys.version, "checks": [], "artifacts": [],
              "real_model_calls": 0, "real_training_jobs": 0}

    def run(name: str, command: list[str], cwd: Path = output) -> None:
        log = output / f"{name}.log"
        with log.open("w", encoding="utf-8") as stream:
            result = subprocess.run(command, cwd=cwd, env=environment, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=180)
        report["checks"].append({"name": name, "exit_code": result.returncode, "log": log.name})
        if result.returncode:
            raise RuntimeError(f"{name} failed; see {log}")
        print(f"PASS {name}", flush=True)

    try:
        wheels = output / "wheels"
        wheels.mkdir()
        for name, (relative, module, executable) in PACKAGES.items():
            source = output / f"{name}-build"
            shutil.copytree(ROOT / relative, source,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info",
                                                        "build", "dist", ".venv", ".git"))
            run(f"{name}-build", [sys.executable, "-m", "pip", "wheel", "--no-deps",
                                 "--no-build-isolation", "--wheel-dir", str(wheels), str(source)])
            distribution = "galatea_mcp" if name == "mcp" else "training_agent_runtime"
            wheel = next(wheels.glob(f"{distribution}-*.whl"))
            # Remove the build copy before importing anything from the installed distribution.
            shutil.rmtree(source)
            virtual = output / f"{name}-venv"
            run(f"{name}-venv", [sys.executable, "-m", "venv", str(virtual)])
            python = str(virtual / "bin/python")
            run(f"{name}-install", [python, "-m", "pip", "install", "--no-index",
                                   "--find-links", str(wheelhouse), str(wheel)])
            run(f"{name}-dependencies", [python, "-m", "pip", "check"])
            run(f"{name}-cli", [str(virtual / "bin" / executable), "--help"])
            forbidden = ["ray", "mlflow", "openai_codex", "training_agent" if name == "mcp" else "galatea_mcp"]
            probe = (
                "import importlib, importlib.util, json, pathlib, sys; "
                f"m=importlib.import_module({module!r}); "
                "assert pathlib.Path(m.__file__).is_relative_to(sys.prefix); "
                f"assert all(importlib.util.find_spec(n) is None for n in {forbidden!r}); "
            )
            if name == "mcp":
                probe += (
                    "p=pathlib.Path(sys.prefix)/'share/galatea-mcp/contracts'; "
                    "assert len(list(p.glob('*.json')))==3; "
                    "[json.loads(f.read_text()) for f in p.glob('*.json')]; "
                )
            else:
                probe += "from training_agent.runtime.output import SCHEMA; assert SCHEMA['type']=='object'; "
            probe += "print(m.__file__)"
            run(f"{name}-isolation", [python, "-I", "-c", probe])
            test_root = output / f"{name}-tests"
            shutil.copytree(ROOT / relative / "tests", test_root,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            run(f"{name}-tests", [python, "-m", "unittest", "discover", "-s", str(test_root), "-v"])
            run(f"{name}-freeze", [python, "-m", "pip", "freeze", "--all"])
            report["artifacts"].append({"path": str(wheel.relative_to(output)),
                                        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()})
        archive = output / "galatea-training-skills.tar.gz"
        run("skills-build", [sys.executable, str(ROOT / "packages/galatea-training-skills/scripts/package.py"), str(archive)])
        with tarfile.open(archive) as bundle:
            bundle.extractall(output / "skills", filter="data")
        skill_root = output / "skills/galatea-training-skills"
        skill_python = str(output / "runner-venv/bin/python")
        run("skills-isolation", [skill_python, "-I", str(skill_root / "scripts/validate.py"), str(skill_root)])
        run("skills-tests", [skill_python, "-m", "unittest", "discover", "-s", str(skill_root / "tests"), "-v"])
        config = json.loads((ROOT / "agents/training-agent-runtime/deploy/config.example.json").read_text())
        installed_relative = Path(config["skill_path"]).relative_to("/opt/galatea-training-skills")
        if not (skill_root / installed_relative).is_file():
            raise ValueError("Runner deployment Skill path does not match the extracted bundle")
        report["artifacts"].append({"path": archive.name, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()})
        report["status"] = "passed"
    except Exception:
        report["status"] = "failed"
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (output / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
