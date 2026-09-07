import hashlib


LINEAGE_KEYS = ("project_id", "campaign_id", "operation_id", "submission_id", "config_id",
                "config_digest", "release_id", "release_digest", "dataset_digest", "split_digest",
                "preprocessing", "metric_definition", "evaluation_protocol", "seed", "role",
                "readiness_digest", "candidate_id", "champion_run_id", "clean_start")


def build_evidence(binding, run_id, rmse, model_bytes, *, roundtrip, load_verified):
    metric = "test_rmse" if binding["role"] == "evaluate" else "val_rmse"
    lineage = {key: binding.get(key) for key in LINEAGE_KEYS}
    if binding["role"] == "evaluate":
        lineage["model_sha256"] = hashlib.sha256(model_bytes).hexdigest()
    return {"schema_version": "galatea.evidence/v1", "lineage": lineage,
            "artifacts": [{"path": "model/model.json", "sha256": hashlib.sha256(model_bytes).hexdigest(),
                           "size_bytes": len(model_bytes)}],
            "integrity": {"roundtrip": bool(roundtrip), "load_verified": bool(load_verified)},
            "metrics": {metric: float(rmse)},
            "final_test_status": "passed" if binding["role"] == "evaluate" else "not-run"}

