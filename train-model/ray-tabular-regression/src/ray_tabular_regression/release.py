import hashlib
import json
import shutil
import zipfile
from pathlib import Path


INCLUDED = ("src", "scripts/driver.py", "configs/baseline.json", "pyproject.toml", "conda.yaml", "galatea.project.yaml")


def build_release(root, output, public_key_pem):
    root, output = Path(root).resolve(), Path(output).resolve()
    if root == output or root in output.parents:
        raise ValueError("release output must be outside project source tree")
    if output.exists():
        raise FileExistsError("immutable release output already exists")
    files = []
    for item in INCLUDED:
        path = root / item
        files.extend(sorted(path.rglob("*")) if path.is_dir() else [path])
    entries = [(str(path.relative_to(root)), path.read_bytes()) for path in files
               if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts
               and path.suffix != ".pyc"]
    entries.append(("release/execution-public.pem", public_key_pem))
    manifest = {name: hashlib.sha256(raw).hexdigest() for name, raw in entries}
    entries.append(("release/manifest.json", json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(entries):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)); info.external_attr = 0o644 << 16
            archive.writestr(info, raw, compress_type=zipfile.ZIP_DEFLATED)
    raw = output.read_bytes()
    return {"path": str(output), "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
            "entrypoint": ["python", "scripts/driver.py"], "deadline_enforced": True}


def write_registration_examples(root, output_dir, release, environment_identity):
    root, output_dir = Path(root), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    source_release = Path(release["path"]).resolve()
    registered_release = output_dir.resolve() / source_release.name
    if registered_release != source_release:
        if registered_release.exists():
            raise FileExistsError("immutable registered release already exists")
        shutil.copyfile(source_release, registered_release)
    project = json.loads((root / "registry" / "project.json").read_text())
    environment_digest = hashlib.sha256(environment_identity.encode()).hexdigest()
    config_source = root / "configs" / "baseline.json"
    config_digest = hashlib.sha256(config_source.read_bytes()).hexdigest()
    project["configs"]["baseline"]["sha256"] = config_digest
    for release_id in ("trainer-v1", "evaluator-v1"):
        item = project["releases"][release_id]
        item.update(path=registered_release.name, sha256=release["sha256"],
                    environment_digest=environment_digest, deadline_enforced=True)
    project["root"] = str(output_dir.resolve())
    project["configs"]["baseline"]["path"] = "configs/baseline.json"
    config_target = output_dir / "configs" / "baseline.json"
    config_target.parent.mkdir(parents=True, exist_ok=True)
    config_target.write_bytes(config_source.read_bytes())
    project_path = output_dir / "project.generated.json"
    campaign_path = output_dir / "campaign.generated.json"
    project_path.write_text(json.dumps(project, indent=2, sort_keys=True) + "\n")
    campaign = json.loads((root / "registry" / "campaign.example.json").read_text())
    campaign_path.write_text(json.dumps(campaign, indent=2, sort_keys=True) + "\n")
    return project_path, campaign_path
