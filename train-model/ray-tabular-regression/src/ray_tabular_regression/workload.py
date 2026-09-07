import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .data import download_json_view, load_role_views, matrix
from .evidence import build_evidence
from .model import dumps, fit_ridge, loads, predict
from .admission import require as _require_admission


def rmse(actual, predicted):
    return math.sqrt(sum((a - p) ** 2 for a, p in zip(actual, predicted)) / len(actual))


def require_integrity(expected, downloaded, *, load_verified):
    if downloaded != expected or not load_verified:
        raise ValueError("MLflow Artifact round-trip or fresh-process load failed")


def download_proxy_artifact(client, run_id, artifact_path, max_bytes=64 * 1024 * 1024):
    if artifact_path.startswith("/") or ".." in Path(artifact_path).parts:
        raise ValueError("unsafe artifact path")
    uri = urlsplit(client.get_run(run_id).info.artifact_uri)
    if uri.scheme != "mlflow-artifacts" or uri.netloc:
        raise ValueError("MLflow server Artifact proxy required")
    entries = client.list_artifacts(run_id, str(Path(artifact_path).parent))
    item = next((entry for entry in entries if entry.path == artifact_path and not entry.is_dir), None)
    if item is None or type(item.file_size) is not int or not 0 <= item.file_size <= max_bytes:
        raise ValueError("artifact missing or exceeds size limit")
    with tempfile.TemporaryDirectory(prefix="galatea-workload-artifact-") as temp:
        downloaded = Path(client.download_artifacts(run_id, artifact_path, dst_path=temp))
        root = Path(temp).resolve()
        if (downloaded.is_symlink() or not downloaded.resolve().is_relative_to(root)
                or not downloaded.is_file() or downloaded.stat().st_size != item.file_size):
            raise ValueError("unsafe or inconsistent downloaded artifact")
        return downloaded.read_bytes()


def run(binding, config, s3_client, mlflow_client, *, admission=None):
    """Called only after driver binding verification; owns the sole MLflow Run."""
    _require_admission(admission, binding)
    if binding.get("model_artifact_path", "model/model.json") != "model/model.json":
        raise ValueError("reference workload supports only model/model.json")
    run = mlflow_client.create_run(binding["experiment_id"], tags={
        "galatea.project": binding["project_id"], "galatea.campaign": binding["campaign_id"],
        "galatea.operation": binding["operation_id"], "galatea.submission": binding["submission_id"],
        "galatea.role": binding["role"], "galatea.release": binding["release_id"],
        "galatea.readiness": binding["readiness_digest"]})
    run_id = run.info.run_id
    try:
        metadata = {"code_revision": binding["code_revision"], "environment_digest": binding["environment_digest"],
                    "dataset_digest": binding["dataset_digest"], "split_digest": binding["split_digest"],
                    "config_digest": binding["config_digest"], "seed": binding["seed"],
                    "resources": json.dumps(binding["resources"], sort_keys=True),
                    "hyperparameters": json.dumps({"alpha": config["alpha"]}, sort_keys=True)}
        for key, value in metadata.items():
            mlflow_client.log_param(run_id, key, value)
        names, target = config["features"], config["target"]
        if binding["role"] == "evaluate":
            artifact_path = binding.get("model_artifact_path", "model/model.json")
            model_bytes = download_proxy_artifact(mlflow_client, binding["champion_run_id"], artifact_path)
            if __import__("hashlib").sha256(model_bytes).hexdigest() != binding["champion_model_sha256"]:
                raise ValueError("champion artifact digest mismatch")
            model = loads(model_bytes)
            views = load_role_views(binding, lambda ref: download_json_view(ref, s3_client))
            x, y = matrix(views["test"], names, target)
        else:
            views = load_role_views(binding, lambda ref: download_json_view(ref, s3_client))
            x, y = matrix(views["train"], names, target)
            model = fit_ridge(x, y, config["alpha"], admission=admission, binding=binding); model_bytes = dumps(model)
            x, y = matrix(views["validation"], names, target)
        value = rmse(y, predict(model, x))
        mlflow_client.log_metric(run_id, "test_rmse" if binding["role"] == "evaluate" else "val_rmse", value)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model.json"; path.write_bytes(model_bytes)
            mlflow_client.log_artifact(run_id, str(path), "model")
            downloaded = download_proxy_artifact(mlflow_client, run_id, "model/model.json")
            roundtrip = downloaded == model_bytes
            roundtrip_path = Path(temp) / "roundtrip.json"; roundtrip_path.write_bytes(downloaded)
            code = ("import json,sys; m=json.load(open(sys.argv[1])); x=[0.25]*len(m['weights']); "
                    "y=m['intercept']+sum(w*((v-a)/b) for w,v,a,b in zip(m['weights'],x,m['means'],m['scales'])); "
                    "assert isinstance(y,(int,float))")
            loaded = subprocess.run(
                [sys.executable, "-I", "-c", code,
                 str(roundtrip_path)],
                check=False, timeout=max(1, binding.get("runtime_max_total_seconds", 30))).returncode == 0
            require_integrity(model_bytes, downloaded, load_verified=loaded)
            report = build_evidence(binding, run_id, value, model_bytes, roundtrip=roundtrip, load_verified=loaded)
            report_path = Path(temp) / "evidence.json"; report_path.write_text(json.dumps(report, sort_keys=True))
            mlflow_client.log_artifact(run_id, str(report_path), "reports")
        mlflow_client.set_terminated(run_id, "FINISHED")
        return report
    except Exception:
        mlflow_client.set_terminated(run_id, "FAILED")
        raise
