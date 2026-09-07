#!/usr/bin/env python3
"""The sole governed Ray Job entrypoint. Direct/local execution fails closed."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ray_tabular_regression.driver import execute
from ray_tabular_regression.binding import verify_execution_binding
from ray_tabular_regression.config import load_bound_config
from ray_tabular_regression.workload import run


def main():
    import boto3
    import mlflow
    import ray
    import yaml
    public_key = (ROOT / "release" / "execution-public.pem").read_bytes()
    binding = verify_execution_binding(os.environ.get("GALATEA_EXECUTION_BINDING"),
                                       os.environ.get("GALATEA_EXECUTION_SIGNATURE"), public_key)
    ray.init(address="auto")
    context = ray.get_runtime_context()

    def official(binding, admission):
        config = load_bound_config(ROOT / binding["config_path"], binding)
        mlflow.set_tracking_uri(binding["tracking_uri"])
        client = mlflow.MlflowClient()
        s3 = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL"))
        return run(binding, config, s3, client, admission=admission)

    print(json.dumps(execute(public_key, context, fit=official), sort_keys=True))


if __name__ == "__main__":
    main()
