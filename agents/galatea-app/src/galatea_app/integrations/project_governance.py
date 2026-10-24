"""Read authority from fixed project receipts and Ray/MLflow APIs, never service files."""

from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import subprocess
import time
from urllib.parse import urlsplit
import httpx
from galatea_app.common import require, utc, digest, timestamp, TaskError
from galatea_app.budgets import within_limits

_CONFLICTING_PROJECT_ID = object()


class ProjectGovernance:
    def __init__(
        self,
        *,
        project_id,
        profile,
        ray_api,
        tracking_uri,
        receipts_root,
        grants=None,
        project_root=None,
        user_authorization=None,
    ):
        for url in (ray_api, tracking_uri):
            p = urlsplit(url)
            require(
                p.scheme in {"http", "https"}
                and p.hostname
                and not (p.username or p.password or p.query or p.fragment),
                "INVALID_COMMAND",
                "Explicit API profile required",
            )
        self.project_id, self.profile, self.ray_api, self.tracking_uri = (
            project_id,
            profile,
            ray_api.rstrip("/"),
            tracking_uri.rstrip("/"),
        )
        self.receipts_root = Path(receipts_root).resolve()
        self.grants = grants or []
        self.user_authorization = user_authorization or {}
        self.operations = {}
        self.http = httpx.Client(timeout=15, follow_redirects=False)
        self.project_root = Path(project_root).resolve() if project_root else None
        self.refresh_receipts()

    def refresh_receipts(self):
        # Receipts are from the configured fixed entrypoint, never model-supplied URLs.
        for path in sorted(self.receipts_root.glob("*-submission.json")):
            plan = path.with_name(
                path.name.removesuffix("-submission.json") + "-plan.json"
            )
            if not plan.is_file():
                continue
            try:
                self.register_receipt(path.name, plan_relative=plan.name)
            except (ValueError, KeyError, OSError, TaskError):
                # Foreign profiles and incomplete writes do not become owned operations.
                continue

    def reconcile(self, intent):
        self.refresh_receipts()
        matches = [
            record for record in self.operations.values() if self.owns(intent, record)
        ]
        require(
            len(matches) <= 1,
            "EXECUTION_UNKNOWN",
            "Multiple receipts match the same attempt",
        )
        return matches[0] if matches else None

    def authorized(self, proposal):
        import time

        inputs = proposal["immutable_inputs"]
        return any(
            g.get("immutable_inputs") == inputs
            and timestamp(g["expires_at"]) > time.time()
            and within_limits(
                proposal["hard_limits"], g["hard_limits"],
                allow_unbounded_memory=g.get("allow_unbounded_memory", False),
            )
            for g in self.grants
        )

    def can_authorize(self, principal_id, proposal):
        """Administrator delegation permits a user decision, never an Agent grant."""
        import time

        policy = self.user_authorization
        inputs = proposal["immutable_inputs"]
        try:
            return bool(
                principal_id in policy["principal_ids"]
                and inputs["route"] == "project_governance"
                and inputs["project_id"] == self.project_id
                and inputs["profile"] == self.profile
                and inputs["role"] in policy["roles"]
                and type(inputs["final_test"]) is bool
                and (not inputs["final_test"] or policy.get("allow_final_test") is True)
                and ("immutable_inputs" not in policy
                     or inputs == policy["immutable_inputs"])
                and time.time() < timestamp(proposal["expires_at"])
                <= timestamp(policy["expires_at"])
                and within_limits(
                    proposal["hard_limits"], policy["hard_limits"],
                    allow_unbounded_memory=policy.get("allow_unbounded_memory", False),
                )
                and all(
                    type(proposal["resources"][k]) in (int, float)
                    and 0 <= proposal["resources"][k] <= policy["resources"][k]
                    for k in ("cpu", "gpu")
                )
            )
        except (KeyError, TypeError, ValueError):
            return False

    def register_receipt(self, relative, *, plan_relative=None):
        path = (self.receipts_root / relative).resolve()
        require(path.is_relative_to(self.receipts_root) and path.is_file(), "FORBIDDEN")
        receipt = json.loads(path.read_text())
        target = receipt["target"]
        submitted = receipt["submitted"]
        require(submitted["ok"], "EVIDENCE_REJECTED")
        require(
            target["rayApiServer"].rstrip("/") == self.ray_api
            and target["mlflowTrackingUri"].rstrip("/") == self.tracking_uri
            and target["clusterProfile"] == self.profile,
            "FORBIDDEN",
            "Receipt belongs to another target profile",
        )
        data = submitted["data"]
        id = data["submissionId"]
        require(re.fullmatch(r"[A-Za-z0-9_-]+", id), "INVALID_COMMAND")
        record = {
            "id": id,
            "job_id": id,
            "execution_identity": data["idempotencyKey"],
            "readiness_digest": data["readinessEvidenceDigest"],
            "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "receipt_path": str(path),
            "target": target,
        }
        if plan_relative:
            pp = (self.receipts_root / plan_relative).resolve()
            require(pp.is_relative_to(self.receipts_root), "FORBIDDEN")
            plan = json.loads(pp.read_text())["planned"]["data"]
            require(
                plan["identity"] == record["execution_identity"]
                and plan["evidence"]["digest"] == record["readiness_digest"],
                "EVIDENCE_REJECTED",
            )
            record["plan"] = plan
        self.operations[id] = record
        return record

    def owns(self, intent, operation):
        registered = self.operations.get(operation.get("id"))
        if not registered:
            return False
        plan = registered.get("plan")
        inputs = intent["immutable_inputs"]
        if not plan:
            return False
        return (
            operation.get("execution_identity") == registered["execution_identity"]
            and intent["idempotency_key"] == plan["attempt"]
            and inputs["role"] == plan["role"]
            and inputs["final_test"] == (plan["role"] == "champion")
            and inputs["project_id"] == self.project_id
            and inputs["profile"] == self.profile
            and inputs["release_digest"] == plan["releaseId"]
            and inputs["config_digest"] == plan["plan"]["configDigest"]
            and inputs["data_digest"] == plan["plan"]["dataset"]["content_sha256"]
            and inputs["split_digest"] == plan["plan"]["dataset"]["split_sha256"]
        )

    def observe(self, operation):
        id = operation["id"]
        require(id in self.operations, "FORBIDDEN")
        response = self.http.get(self.ray_api + "/api/jobs/" + id)
        if response.status_code != 200:
            return {
                "status": "unknown",
                "reason": "Ray observation unavailable",
                "observed_at": utc(),
            }
        job = response.json()
        state = {
            "PENDING": "queued",
            "RUNNING": "running",
            "SUCCEEDED": "succeeded",
            "FAILED": "failed",
            "STOPPED": "cancelled",
        }.get(job["status"], "unknown")
        from mlflow import MlflowClient

        client = MlflowClient(tracking_uri=self.tracking_uri)
        experiment = client.get_experiment_by_name(self.project_id)
        runs = (
            client.search_runs(
                [experiment.experiment_id],
                filter_string=f"tags.`galatea.submission.id` = '{id}'",
                max_results=2,
            )
            if experiment
            else []
        )
        fact = {
            "status": state,
            "ray_status": job["status"],
            "ray_job_id": job.get("job_id"),
            "observed_at": utc(),
            "metrics": [],
        }
        if len(runs) == 1:
            run = runs[0]
            tags = run.data.tags
            params = run.data.params
            record = self.operations[id]
            require(
                tags.get("galatea.project") == self.project_id
                and tags.get("galatea.execution.identity")
                == record["execution_identity"]
                and tags.get("galatea.readiness.digest") == record["readiness_digest"],
                "EVIDENCE_REJECTED",
                "Run is not bound to governed receipt",
            )
            fact.update(
                run_id=run.info.run_id,
                mlflow_status=run.info.status,
                role=tags.get("run.role"),
                artifact_roundtrip=tags.get("artifact.roundtrip_verified") == "true",
                lineage={
                    k: params.get(k)
                    for k in [
                        "data.content_sha256",
                        "data.split_sha256",
                        "data.preprocessing_version",
                        "training.epochs",
                    ]
                },
                release_id=tags.get("galatea.release.id"),
                test_access=tags.get("test.access", "not_reported"),
            )
            if state == "succeeded" and (
                run.info.status != "FINISHED"
                or tags.get("run.outcome") != "succeeded"
                or tags.get("artifact.roundtrip_verified") != "true"
            ):
                fact["status"] = "unknown"
                fact["reason"] = "Ray and MLflow evidence are inconsistent"
            for name, value in run.data.metrics.items():
                if name.startswith(("train_", "val_")) and not name.startswith("test_"):
                    history = client.get_metric_history(run.info.run_id, name)
                    latest = (
                        max(history, key=lambda x: (x.step, x.timestamp))
                        if history
                        else None
                    )
                    fact["metrics"].append(
                        {
                            "name": name,
                            "value": value,
                            "split": (
                                "validation" if name.startswith("val_") else "train"
                            ),
                            "step": latest.step if latest else 0,
                            "observed_at": utc(),
                        }
                    )
        elif state == "succeeded":
            fact["status"] = "unknown"
            fact["reason"] = "Unique bound MLflow Run not found"
            # Allow tracking visibility to settle, but a terminal Job without
            # its own Run must not leave cancellation or recovery pending forever.
            ended = job.get("end_time")
            if (
                type(ended) in (int, float)
                and math.isfinite(ended)
                and 0 < ended / 1000 <= time.time() - 60
            ):
                fact["status"] = "failed"
                fact["reason"] = "Terminal Ray Job has no unique bound MLflow Run"
        return fact

    def execution_started_at(self, operation):
        """Return the Ray-owned start timestamp; never trust model-supplied time."""
        require(operation["id"] in self.operations, "FORBIDDEN")
        response = self.http.get(self.ray_api + "/api/jobs/" + operation["id"])
        response.raise_for_status()
        started = response.json().get("start_time")
        require(
            type(started) in (int, float)
            and math.isfinite(started)
            and 0 < started / 1000 <= time.time(),
            "EXECUTION_UNKNOWN",
            "Ray has not supplied an execution start timestamp",
        )
        return started / 1000

    def read_artifact(self, ref, max_bytes):
        require(
            ref.get("route") == "project_governance"
            and ref.get("operation_id") in self.operations,
            "FORBIDDEN",
        )
        path = Path(ref["path"])
        require(
            not path.is_absolute()
            and ".." not in path.parts
            and path.parts
            and path.parts[0] in {"reports", "manifests", "config", "metadata"},
            "FORBIDDEN",
            "Only registered report namespaces may be previewed",
        )
        fact = self.observe({"id": ref["operation_id"]})
        require(fact.get("run_id") == ref["run_id"], "FORBIDDEN")
        from mlflow import MlflowClient

        client = MlflowClient(tracking_uri=self.tracking_uri)
        entries = client.list_artifacts(ref["run_id"], str(path.parent))
        entry = next((x for x in entries if x.path == str(path)), None)
        require(
            entry and not entry.is_dir and entry.file_size <= max_bytes,
            "RATE_LIMITED",
            "Artifact unavailable or oversized",
        )
        with tempfile.TemporaryDirectory(prefix="galatea-artifact-") as directory:
            output = Path(
                client.download_artifacts(ref["run_id"], str(path), directory)
            )
            data = output.read_bytes()
        require(len(data) <= max_bytes, "RATE_LIMITED")
        return data

    def stop(self, operation):
        require(
            self.project_root is not None,
            "CAPABILITY_UNAVAILABLE",
            "Project stop entrypoint requires project_root",
        )
        record = self.operations.get(operation["id"])
        require(record is not None, "FORBIDDEN")
        runner = Path(__file__).parents[3] / "scripts/project_stop.mjs"
        result = subprocess.run(
            [
                "node",
                str(runner),
                str(self.project_root),
                record["receipt_path"],
                "Task cancellation",
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        require(
            result.returncode == 0,
            "UNAVAILABLE",
            "Governed stop entrypoint unavailable; continue observing",
        )
        response = json.loads(result.stdout)
        require(response.get("ok"), "UNAVAILABLE", "Controller rejected stop request")
        return response


class ProjectGovernanceRegistry:
    """Route project-governed work to isolated per-project adapters.

    A project adapter owns its receipts, API endpoints, authorization policy and
    operation index.  The registry is the single service-facing capability and
    selects an adapter from the immutable project identity carried by an intent,
    proposal, operation or artifact reference.  The legacy one-project config is
    still supported by :func:`galatea_app.cli.build`, but new deployments should
    use the ``projects`` mapping.
    """

    def __init__(self, projects):
        require(isinstance(projects, dict) and projects, "INVALID_COMMAND",
                "At least one project governance adapter is required")
        self.adapters = {}
        for project_id, settings in projects.items():
            require(
                isinstance(project_id, str) and project_id and isinstance(settings, dict),
                "INVALID_COMMAND",
                "Project governance entries must be named objects",
            )
            settings = dict(settings)
            require(
                settings.get("project_id") == project_id,
                "INVALID_COMMAND",
                "Project registry key must match project_id",
            )
            require(project_id not in self.adapters, "INVALID_COMMAND")
            self.adapters[project_id] = ProjectGovernance(**settings)

    @property
    def project_ids(self):
        return tuple(self.adapters)

    def _project_id(self, value):
        if not isinstance(value, dict):
            return None
        inputs = value.get("immutable_inputs")
        immutable_project = (
            inputs.get("project_id")
            if isinstance(inputs, dict)
            and isinstance(inputs.get("project_id"), str)
            else None
        )
        direct_project = (
            value.get("project_id")
            if isinstance(value.get("project_id"), str)
            else None
        )
        if immutable_project and direct_project and immutable_project != direct_project:
            return _CONFLICTING_PROJECT_ID
        if immutable_project:
            return immutable_project
        if direct_project:
            return direct_project
        target = value.get("target")
        if isinstance(target, dict) and isinstance(target.get("project_id"), str):
            return target["project_id"]
        return None

    def _adapter_for(self, value, *, required=True):
        project_id = self._project_id(value)
        if project_id is _CONFLICTING_PROJECT_ID:
            if required:
                require(False, "FORBIDDEN", "Project identity fields do not match")
            return None
        if project_id in self.adapters:
            return self.adapters[project_id]

        # Historical operation records did not persist project_id.  Resolve
        # those records by the adapter's receipt index, but never guess between
        # projects when an identity is present and unknown.
        operation_id = value.get("operation_id") if isinstance(value, dict) else None
        if operation_id is None and isinstance(value, dict):
            operation_id = value.get("id")
        if not project_id and operation_id:
            matches = [
                adapter for adapter in self.adapters.values()
                if operation_id in adapter.operations
            ]
            if len(matches) == 1:
                return matches[0]
        if required:
            require(
                False,
                "CAPABILITY_UNAVAILABLE",
                "No project governance adapter is registered for this project",
            )
        return None

    def authorized(self, proposal):
        adapter = self._adapter_for(proposal, required=False)
        return bool(adapter and adapter.authorized(proposal))

    def can_authorize(self, principal_id, proposal):
        adapter = self._adapter_for(proposal, required=False)
        return bool(adapter and adapter.can_authorize(principal_id, proposal))

    def reconcile(self, intent):
        return self._adapter_for(intent).reconcile(intent)

    def owns(self, intent, operation):
        intent_project = self._project_id(intent)
        operation_project = self._project_id(operation)
        if (
            intent_project is _CONFLICTING_PROJECT_ID
            or operation_project is _CONFLICTING_PROJECT_ID
            or (
                intent_project
                and operation_project
                and intent_project != operation_project
            )
        ):
            return False
        if isinstance(intent_project, str) and intent_project not in self.adapters:
            return False
        adapter = self._adapter_for(intent, required=False)
        if adapter:
            return adapter.owns(intent, operation)
        # Keep reconciliation fail-closed for unknown projects, while allowing
        # old records whose adapter identity was not persisted to be checked.
        return any(
            candidate.owns(intent, operation) for candidate in self.adapters.values()
        )

    def observe(self, operation):
        return self._adapter_for(operation).observe(operation)

    def execution_started_at(self, operation):
        return self._adapter_for(operation).execution_started_at(operation)

    def read_artifact(self, ref, max_bytes):
        return self._adapter_for(ref).read_artifact(ref, max_bytes)

    def stop(self, operation):
        return self._adapter_for(operation).stop(operation)

    def close(self):
        for adapter in self.adapters.values():
            adapter.http.close()
