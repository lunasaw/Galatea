#!/usr/bin/env python3
"""Analyze an MLflow experiment through the Tracking API without changing state."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from mlflow import MlflowClient


OBJECTIVE_CANDIDATES = (
    ("best_val_accuracy", "max"),
    ("best_validation_accuracy", "max"),
    ("val_accuracy", "max"),
    ("validation_accuracy", "max"),
    ("eval_accuracy", "max"),
    ("best_val_auc", "max"),
    ("val_auc", "max"),
    ("validation_auc", "max"),
    ("best_val_loss", "min"),
    ("best_validation_loss", "min"),
    ("val_loss", "min"),
    ("validation_loss", "min"),
    ("eval_loss", "min"),
)
COMMON_COHORT_PARAMS = (
    "data.content_sha256",
    "dataset.content_sha256",
    "data.split_sha256",
    "dataset.split_sha256",
    "dataset_digest",
    "data.dataset_version",
    "dataset.version",
)
EPOCH_PARAM_KEYS = (
    "training.epochs_requested",
    "epochs",
    "num_epochs",
    "max_epochs",
    "trainer.max_epochs",
)
MINIMIZE_TOKENS = (
    "loss",
    "error",
    "rmse",
    "mse",
    "mae",
    "wer",
    "cer",
    "perplexity",
    "latency",
    "duration",
    "cost",
)
MAXIMIZE_TOKENS = ("accuracy", "auc", "f1", "precision", "recall", "map", "ndcg")
METADATA_PARAM_PARTS = (
    "sha256",
    "digest",
    "dataset_version",
    "source_uri",
    "artifact_uri",
    "run_id",
    "timestamp",
    "parent_run",
    "history_run_count",
    "parameter_count",
    "num_parameters",
    "output_classes",
    "training_images",
    "validation_images",
    "test_images",
)
BASE_TAG_KEYS = (
    "run.outcome",
    "quality_gate.passed",
    "artifact.roundtrip_verified",
    "code.git_commit",
    "code.git_dirty",
    "mlflow.source.name",
    "mlflow.source.type",
    "model.uri",
    "tuning.role",
    "tuning.study_name",
)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    result = _number(value)
    return int(result) if result is not None else None


def _boolean(value: Any) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return None


def _time_text(timestamp_ms: int | None, zone: ZoneInfo) -> str | None:
    if not timestamp_ms:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).astimezone(zone).isoformat()


def _reject_backend_store_uri(tracking_uri: str, allow_test_store: bool) -> None:
    if allow_test_store:
        return
    lowered = tracking_uri.strip().lower()
    if lowered.startswith(("sqlite:", "file:", "/", "./", "../")):
        raise ValueError(
            "Tracking URI must address an MLflow Tracking API, not a backend database or local file store"
        )


def _dataset_inputs(run: Any) -> list[dict[str, str | None]]:
    inputs = getattr(run, "inputs", None)
    dataset_inputs = getattr(inputs, "dataset_inputs", []) if inputs else []
    records: list[dict[str, str | None]] = []
    for dataset_input in dataset_inputs:
        context = None
        for tag in getattr(dataset_input, "tags", []) or []:
            if tag.key == "mlflow.data.context":
                context = tag.value
                break
        dataset = dataset_input.dataset
        records.append(
            {
                "context": context,
                "name": dataset.name,
                "digest": dataset.digest,
                "source_type": dataset.source_type,
            }
        )
    return sorted(
        records,
        key=lambda item: (
            item.get("context") or "",
            item.get("name") or "",
            item.get("digest") or "",
        ),
    )


def _model_outputs(run: Any) -> list[str]:
    outputs = getattr(run, "outputs", None)
    model_outputs = getattr(outputs, "model_outputs", []) if outputs else []
    return [output.model_id for output in model_outputs]


def _run_record(
    run: Any, zone: ZoneInfo, requested_tag_keys: set[str]
) -> dict[str, Any]:
    metrics = {
        key: value
        for key, raw_value in run.data.metrics.items()
        if (value := _number(raw_value)) is not None
    }
    tag_keys = set(BASE_TAG_KEYS) | requested_tag_keys
    tags = {
        key: run.data.tags[key]
        for key in sorted(tag_keys)
        if key in run.data.tags
    }
    return {
        "run_id": run.info.run_id,
        "run_name": run.info.run_name,
        "status": run.info.status,
        "start_time_ms": run.info.start_time,
        "started_at": _time_text(run.info.start_time, zone),
        "end_time_ms": run.info.end_time,
        "artifact_uri": run.info.artifact_uri,
        "params": dict(run.data.params),
        "metrics": metrics,
        "tags": tags,
        "datasets": _dataset_inputs(run),
        "logged_model_ids": _model_outputs(run),
    }


def _successful(run: dict[str, Any]) -> bool:
    return run["status"] == "FINISHED" and run["tags"].get("run.outcome") != "failed"


def _is_holdout_metric(name: str) -> bool:
    segments = re.split(r"[./:_-]+", name.lower())
    return any(segment in {"test", "holdout"} for segment in segments)


def _infer_mode(metric: str) -> str:
    name = metric.lower()
    minimize = any(token in name for token in MINIMIZE_TOKENS)
    maximize = any(token in name for token in MAXIMIZE_TOKENS)
    if minimize and not maximize:
        return "min"
    if maximize and not minimize:
        return "max"
    raise ValueError(
        f"Cannot infer direction for objective '{metric}'; pass --objective-mode"
    )


def _resolve_objective(
    runs: list[dict[str, Any]], requested_metric: str | None, requested_mode: str
) -> tuple[str, str]:
    available = {key for run in runs for key in run["metrics"]}
    if requested_metric:
        if requested_metric not in available:
            raise ValueError(
                f"Objective metric '{requested_metric}' is absent; available metrics: "
                + ", ".join(sorted(available))
            )
        mode = _infer_mode(requested_metric) if requested_mode == "auto" else requested_mode
        return requested_metric, mode

    for metric, mode in OBJECTIVE_CANDIDATES:
        if metric in available:
            return metric, mode
    validation_metrics = sorted(
        metric
        for metric in available
        if re.search(r"(^|[./:_-])(val|valid|validation|eval)([./:_-]|$)", metric.lower())
        and not _is_holdout_metric(metric)
    )
    if len(validation_metrics) == 1:
        metric = validation_metrics[0]
        return metric, _infer_mode(metric) if requested_mode == "auto" else requested_mode
    raise ValueError(
        "No unambiguous validation objective was found; pass --objective-metric and --objective-mode"
    )


def _dataset_signature(run: dict[str, Any]) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            str(item.get("context") or ""),
            str(item.get("name") or ""),
            str(item.get("digest") or ""),
        )
        for item in run["datasets"]
    )


def _select_cohort(
    successful: list[dict[str, Any]], requested_params: list[str]
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    latest = successful[0]
    warnings: list[str] = []
    if requested_params:
        missing = [key for key in requested_params if key not in latest["params"]]
        if missing:
            raise ValueError(
                "Latest successful Run lacks requested cohort parameters: "
                + ", ".join(missing)
            )
        keys = requested_params
    else:
        keys = [key for key in COMMON_COHORT_PARAMS if key in latest["params"]]
    if keys:
        values = {key: latest["params"][key] for key in keys}
        cohort = [
            run
            for run in successful
            if all(run["params"].get(key) == value for key, value in values.items())
        ]
        return cohort, {"method": "params", "values": values}, warnings

    signature = _dataset_signature(latest)
    if signature:
        cohort = [run for run in successful if _dataset_signature(run) == signature]
        return cohort, {"method": "mlflow-dataset-inputs", "values": signature}, warnings

    warnings.append(
        "No dataset/split identity was found; all successful filtered Runs are treated as comparable"
    )
    return successful, {"method": "unscoped", "values": {}}, warnings


def _best_run(
    runs: Iterable[dict[str, Any]], objective: str, mode: str
) -> dict[str, Any] | None:
    candidates = [run for run in runs if objective in run["metrics"]]
    if not candidates:
        return None
    key = lambda run: (run["metrics"][objective], run["start_time_ms"] or 0)
    return (max if mode == "max" else min)(candidates, key=key)


def _metric_mean(values: list[float]) -> float:
    return statistics.fmean(values)


def _parameter_group_value(value: str) -> str:
    text = value.strip()
    if text.lower() in {"true", "false", "none", "null"}:
        return text
    number = _number(text)
    return format(number, ".8g") if number is not None else text


def _parameter_analysis(
    runs: list[dict[str, Any]],
    objective: str,
    mode: str,
    cohort_keys: set[str],
    requested_params: list[str],
    max_parameters: int,
) -> list[dict[str, Any]]:
    scored = [run for run in runs if objective in run["metrics"]]
    names = set(requested_params) if requested_params else {
        name for run in scored for name in run["params"]
    }
    summaries: list[dict[str, Any]] = []
    for name in sorted(names):
        if name in cohort_keys:
            continue
        lowered = name.lower()
        if not requested_params and any(part in lowered for part in METADATA_PARAM_PARTS):
            continue
        groups: dict[str, list[float]] = {}
        for run in scored:
            if name in run["params"]:
                value = _parameter_group_value(run["params"][name])
                groups.setdefault(value, []).append(run["metrics"][objective])
        if len(groups) < 2:
            continue
        group_rows = []
        for value, scores in groups.items():
            group_rows.append(
                {
                    "value": value,
                    "run_count": len(scores),
                    "mean_objective": _metric_mean(scores),
                    "best_objective": (max if mode == "max" else min)(scores),
                    "stddev": statistics.pstdev(scores) if len(scores) > 1 else 0.0,
                }
            )
        group_rows.sort(key=lambda row: row["mean_objective"], reverse=mode == "max")
        summaries.append(
            {
                "parameter": name,
                "distinct_values": len(groups),
                "observations": sum(len(scores) for scores in groups.values()),
                "leading_value_by_group_mean": group_rows[0]["value"],
                "groups": group_rows,
            }
        )
    summaries.sort(
        key=lambda item: (item["observations"], item["distinct_values"], item["parameter"]),
        reverse=True,
    )
    return summaries[:max_parameters]


def _validation_curve_metric(objective: str, available: set[str]) -> str | None:
    candidates = [objective]
    if objective.startswith("best_"):
        candidates.insert(0, objective.removeprefix("best_"))
    for candidate in candidates:
        if candidate in available:
            return candidate
    return None


def _infer_train_metric(validation_metric: str | None, available: set[str]) -> str | None:
    if not validation_metric:
        return None
    candidates = [
        validation_metric.replace("validation", "train"),
        validation_metric.replace("valid", "train"),
        validation_metric.replace("val", "train"),
        validation_metric.replace("eval", "train"),
    ]
    for candidate in candidates:
        if candidate in available and candidate != validation_metric:
            return candidate
    return None


def _history_summary(
    client: MlflowClient, run_id: str, metric: str | None, mode: str
) -> dict[str, Any] | None:
    if not metric:
        return None
    history = client.get_metric_history(run_id, metric)
    if not history:
        return None
    by_step: dict[int, Any] = {}
    for item in sorted(history, key=lambda value: (value.step, value.timestamp)):
        by_step[item.step] = item
    points = [by_step[step] for step in sorted(by_step)]
    best = (max if mode == "max" else min)(points, key=lambda item: item.value)
    return {
        "metric": metric,
        "points": len(points),
        "first": points[0].value,
        "last": points[-1].value,
        "best": best.value,
        "best_step": best.step,
        "last_step": points[-1].step,
    }


def _repository_state(repo_root: Path) -> dict[str, Any]:
    if not (repo_root / ".git").exists():
        return {"path": str(repo_root), "available": False}
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    dirty_files = [line for line in result.stdout.splitlines() if line]
    return {
        "path": str(repo_root),
        "available": result.returncode == 0,
        "dirty": bool(dirty_files),
        "dirty_files": dirty_files[:20],
    }


def _epoch_budget(run: dict[str, Any]) -> tuple[str, int] | None:
    for key in EPOCH_PARAM_KEYS:
        value = _integer(run["params"].get(key))
        if value is not None:
            return key, value
    return None


def _target_reached(score: float, target: float | None, mode: str) -> bool | None:
    if target is None:
        return None
    return score >= target if mode == "max" else score <= target


def _recommendations(
    *,
    latest_any: dict[str, Any],
    latest: dict[str, Any],
    best: dict[str, Any],
    cohort: list[dict[str, Any]],
    parameter_analysis: list[dict[str, Any]],
    target_reached: bool | None,
    quality_gate: bool | None,
    artifact_verified: bool | None,
    gap: float | None,
    gap_threshold: float,
    accuracy_like: bool,
    minimum_runs: int,
    cohort_method: str,
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if latest_any["run_id"] != latest["run_id"]:
        items.append(
            {"priority": "P0", "category": "failed-latest", "action": "Diagnose the newer non-successful Run before starting another study."}
        )
    if cohort_method == "unscoped":
        items.append(
            {"priority": "P0", "category": "data-lineage", "action": "Log immutable dataset and split identity before trusting cross-Run comparisons."}
        )
    if quality_gate is False:
        items.append(
            {"priority": "P0", "category": "quality-gate", "action": "Block promotion and improve the candidate; do not lower the gate merely to pass."}
        )
    if artifact_verified is False:
        items.append(
            {"priority": "P0", "category": "artifact-integrity", "action": "Repair Artifact logging and recovery before spending more training compute."}
        )
    if len(cohort) < minimum_runs:
        items.append(
            {"priority": "P1", "category": "search-coverage", "action": f"Collect at least {minimum_runs} controlled compatible observations; only {len(cohort)} are available."}
        )
    if not parameter_analysis:
        items.append(
            {"priority": "P1", "category": "parameter-coverage", "action": "Vary meaningful hyperparameters in a controlled validation-only search; current history cannot attribute improvements."}
        )
    else:
        leaders = ", ".join(
            f"{item['parameter']}={item['leading_value_by_group_mean']}"
            for item in parameter_analysis[:3]
        )
        items.append(
            {"priority": "P2", "category": "parameter-leads", "action": f"Use grouped history only as a lead, then run controlled follow-ups around: {leaders}."}
        )
    epoch_budget = _epoch_budget(latest)
    if epoch_budget and epoch_budget[1] <= 1:
        items.append(
            {"priority": "P1", "category": "training-budget", "action": "Treat the one-Epoch result as a smoke test unless curves prove convergence; use Early Stopping with a larger cap."}
        )
    if target_reached is False:
        items.append(
            {"priority": "P1", "category": "objective-target", "action": "Continue the approved validation search because the declared objective target is not reached."}
        )
    if latest["run_id"] != best["run_id"]:
        items.append(
            {"priority": "P1", "category": "selection", "action": "Do not describe the latest Run as best; retain or cleanly retrain the validation winner."}
        )
    if accuracy_like and gap is not None and gap > gap_threshold:
        items.append(
            {"priority": "P1", "category": "generalization", "action": f"Investigate overfitting: normalized train-validation gap is {gap:.4f}; test regularization, data, and stopping changes."}
        )
    if _boolean(best["tags"].get("code.git_dirty")) is True:
        items.append(
            {"priority": "P2", "category": "reproducibility", "action": "Reproduce the selected configuration from a clean source revision before promotion."}
        )
    order = {"P0": 0, "P1": 1, "P2": 2}
    return sorted(items, key=lambda item: (order[item["priority"]], item["category"]))


def analyze(arguments: argparse.Namespace) -> dict[str, Any]:
    _reject_backend_store_uri(
        arguments.tracking_uri,
        bool(getattr(arguments, "allow_backend_store_for_tests", False)),
    )
    zone = ZoneInfo(arguments.timezone)
    client = MlflowClient(tracking_uri=arguments.tracking_uri)
    experiment = (
        client.get_experiment(arguments.experiment_id)
        if arguments.experiment_id
        else client.get_experiment_by_name(arguments.experiment)
    )
    if experiment is None:
        identifier = arguments.experiment_id or arguments.experiment
        raise RuntimeError(f"MLflow experiment not found: {identifier}")
    runs = client.search_runs(
        experiment_ids=[experiment.experiment_id],
        filter_string=arguments.filter or "",
        order_by=["attributes.start_time DESC"],
        max_results=arguments.max_runs,
    )
    requested_tag_keys = {
        arguments.quality_gate_tag,
        arguments.artifact_verification_tag,
    }
    records = [_run_record(run, zone, requested_tag_keys) for run in runs]
    successful = [run for run in records if _successful(run)]
    if not successful:
        raise RuntimeError("No successful Runs match the Experiment and filter")

    objective, mode = _resolve_objective(
        successful, arguments.objective_metric, arguments.objective_mode
    )
    if _is_holdout_metric(objective) and not arguments.allow_holdout_objective:
        raise ValueError(
            f"Refusing holdout objective '{objective}'; use a validation metric or pass --allow-holdout-objective explicitly"
        )
    cohort, cohort_identity, warnings = _select_cohort(
        successful, arguments.cohort_param
    )
    scored = [run for run in cohort if objective in run["metrics"]]
    if not scored:
        raise RuntimeError("No compatible successful Run contains the objective metric")
    latest = successful[0]
    best = _best_run(scored, objective, mode)
    assert best is not None

    cohort_keys = set(cohort_identity["values"]) if cohort_identity["method"] == "params" else set()
    parameter_analysis = _parameter_analysis(
        scored,
        objective,
        mode,
        cohort_keys,
        arguments.parameter,
        arguments.max_parameters,
    )
    available = set(best["metrics"])
    validation_metric = arguments.validation_metric or _validation_curve_metric(objective, available)
    train_metric = arguments.train_metric or _infer_train_metric(validation_metric, available)
    validation_curve = _history_summary(client, best["run_id"], validation_metric, mode)
    train_curve = _history_summary(client, best["run_id"], train_metric, mode)
    train_value = best["metrics"].get(train_metric) if train_metric else None
    validation_value = best["metrics"].get(validation_metric) if validation_metric else None
    gap = None
    if train_value is not None and validation_value is not None:
        gap = train_value - validation_value if mode == "max" else validation_value - train_value

    score = best["metrics"][objective]
    reached = _target_reached(score, arguments.target, mode)
    quality_gate = _boolean(best["tags"].get(arguments.quality_gate_tag))
    artifact_verified = _boolean(best["tags"].get(arguments.artifact_verification_tag))
    accuracy_like = any(token in objective.lower() for token in MAXIMIZE_TOKENS)
    holdout_metrics = {
        key: value for key, value in best["metrics"].items() if _is_holdout_metric(key)
    }
    if quality_gate is False or artifact_verified is False:
        status = "not-ready"
    elif arguments.target is not None and reached is False:
        status = "search-incomplete"
    elif len(scored) < arguments.minimum_runs or not parameter_analysis:
        status = "insufficient-evidence"
    elif quality_gate is True and artifact_verified is True:
        status = "candidate-checks-passed"
    else:
        status = "best-observed-not-proven-optimal"

    reasons: list[str] = []
    if len(scored) < arguments.minimum_runs:
        reasons.append("The compatible scored cohort is smaller than the requested evidence minimum")
    if not parameter_analysis:
        reasons.append("No meaningful varied parameter has enough scored observations")
    if reached is False:
        reasons.append("The declared objective target is not reached")
    if quality_gate is False:
        reasons.append("The selected Run explicitly failed its quality gate")
    if artifact_verified is False:
        reasons.append("The selected Run explicitly failed Artifact verification")
    if latest["run_id"] != best["run_id"]:
        reasons.append("The latest successful Run is not the best compatible objective Run")
    if not reasons:
        reasons.append("The best result is bounded by the observed search space, data, and budget")

    leaderboard = sorted(
        scored,
        key=lambda run: run["metrics"][objective],
        reverse=mode == "max",
    )[: arguments.top_runs]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "access_method": "mlflow-tracking-api",
        "tracking_uri": arguments.tracking_uri,
        "experiment": {
            "name": experiment.name,
            "experiment_id": experiment.experiment_id,
            "artifact_location": experiment.artifact_location,
            "filter": arguments.filter or None,
        },
        "objective": {
            "metric": objective,
            "mode": mode,
            "target": arguments.target,
            "target_reached": reached,
            "holdout_objective": _is_holdout_metric(objective),
        },
        "scope": {
            "matched_runs": len(records),
            "successful_runs": len(successful),
            "compatible_runs": len(cohort),
            "scored_runs": len(scored),
            "cohort": cohort_identity,
        },
        "latest_successful_run": latest,
        "best_observed_run": best,
        "leaderboard": leaderboard,
        "parameter_analysis": parameter_analysis,
        "learning_curves": {
            "train": train_curve,
            "validation": validation_curve,
            "normalized_generalization_gap": gap,
        },
        "holdout_metrics_descriptive_only": holdout_metrics,
        "checks": {
            "quality_gate_tag": arguments.quality_gate_tag,
            "quality_gate": quality_gate,
            "artifact_verification_tag": arguments.artifact_verification_tag,
            "artifact_verified": artifact_verified,
            "logged_model_ids": best["logged_model_ids"],
            "model_uri": best["tags"].get("model.uri"),
        },
        "verdict": {
            "status": status,
            "latest_is_best_observed": latest["run_id"] == best["run_id"],
            "global_optimality_claim_supported": False,
            "reasons": reasons,
        },
        "recommendations": _recommendations(
            latest_any=records[0],
            latest=latest,
            best=best,
            cohort=scored,
            parameter_analysis=parameter_analysis,
            target_reached=reached,
            quality_gate=quality_gate,
            artifact_verified=artifact_verified,
            gap=gap,
            gap_threshold=arguments.gap_threshold,
            accuracy_like=accuracy_like,
            minimum_runs=arguments.minimum_runs,
            cohort_method=cohort_identity["method"],
        ),
        "repository": _repository_state(arguments.repo_root),
        "warnings": warnings,
    }


def _score(value: Any) -> str:
    number = _number(value)
    return "-" if number is None else f"{number:.6f}"


def _markdown(result: dict[str, Any]) -> str:
    experiment = result["experiment"]
    objective = result["objective"]
    scope = result["scope"]
    latest = result["latest_successful_run"]
    best = result["best_observed_run"]
    verdict = result["verdict"]
    lines = [
        f"# MLflow optimization analysis: {experiment['name']}",
        "",
        f"- Verdict: `{verdict['status']}`",
        f"- Objective: `{objective['metric']}` ({objective['mode']})",
        f"- Latest successful Run: `{latest['run_id']}`",
        f"- Best observed compatible Run: `{best['run_id']}`",
        f"- Best observed score: {_score(best['metrics'][objective['metric']])}",
        f"- Compatible/scored Runs: {scope['compatible_runs']}/{scope['scored_runs']}",
        f"- Cohort method: `{scope['cohort']['method']}`",
        "",
        "## Leaderboard",
        "",
        "| Run | Started | Role | Objective | Status |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for run in result["leaderboard"]:
        lines.append(
            f"| `{run['run_id'][:12]}` | {run['started_at']} | "
            f"{run['tags'].get('tuning.role') or '-'} | "
            f"{_score(run['metrics'][objective['metric']])} | {run['status']} |"
        )
    lines.extend(["", "## Parameter coverage", ""])
    if result["parameter_analysis"]:
        lines.extend(
            [
                "| Parameter | Values | Observations | Leading grouped value |",
                "| --- | ---: | ---: | --- |",
            ]
        )
        for item in result["parameter_analysis"]:
            lines.append(
                f"| `{item['parameter']}` | {item['distinct_values']} | "
                f"{item['observations']} | `{item['leading_value_by_group_mean']}` |"
            )
    else:
        lines.append("No varied parameter has enough compatible scored observations.")
    holdout = result["holdout_metrics_descriptive_only"]
    if holdout:
        lines.extend(["", "## Holdout metrics (descriptive only)", ""])
        lines.extend(f"- `{key}`: {_score(value)}" for key, value in sorted(holdout.items()))
    curves = result["learning_curves"]
    lines.extend(["", "## Learning evidence", ""])
    for name in ("train", "validation"):
        curve = curves[name]
        if curve:
            lines.append(
                f"- {name}: `{curve['metric']}`, {curve['points']} points, "
                f"best {_score(curve['best'])} at step {curve['best_step']}, "
                f"last {_score(curve['last'])}"
            )
    lines.append(
        f"- Normalized generalization gap: {_score(curves['normalized_generalization_gap'])}"
    )
    lines.extend(["", "## Reasons", ""])
    lines.extend(f"- {reason}" for reason in verdict["reasons"])
    lines.extend(["", "## Ranked actions", ""])
    lines.extend(
        f"- {item['priority']} `{item['category']}`: {item['action']}"
        for item in result["recommendations"]
    )
    if result["warnings"]:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in result["warnings"])
    lines.extend(
        [
            "",
            "Grouped parameter results are correlational leads. Holdout metrics must not drive trial selection.",
        ]
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only, framework-agnostic analysis of an MLflow Experiment."
    )
    parser.add_argument(
        "--tracking-uri",
        default=os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000"),
    )
    experiment_group = parser.add_mutually_exclusive_group(required=True)
    experiment_group.add_argument("--experiment")
    experiment_group.add_argument("--experiment-id")
    parser.add_argument("--filter", default="")
    parser.add_argument("--objective-metric")
    parser.add_argument("--objective-mode", choices=("auto", "max", "min"), default="auto")
    parser.add_argument("--target", type=float)
    parser.add_argument("--cohort-param", action="append", default=[])
    parser.add_argument("--parameter", action="append", default=[])
    parser.add_argument("--train-metric")
    parser.add_argument("--validation-metric")
    parser.add_argument("--quality-gate-tag", default="quality_gate.passed")
    parser.add_argument(
        "--artifact-verification-tag", default="artifact.roundtrip_verified"
    )
    parser.add_argument("--allow-holdout-objective", action="store_true")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--max-runs", type=int, default=5000)
    parser.add_argument("--top-runs", type=int, default=10)
    parser.add_argument("--max-parameters", type=int, default=20)
    parser.add_argument("--minimum-runs", type=int, default=4)
    parser.add_argument("--gap-threshold", type=float, default=0.05)
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    return parser


def main() -> None:
    parser = _parser()
    arguments = parser.parse_args()
    for name in ("max_runs", "top_runs", "max_parameters", "minimum_runs"):
        if getattr(arguments, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if arguments.gap_threshold < 0:
        parser.error("--gap-threshold must be non-negative")
    try:
        result = analyze(arguments)
    except Exception as error:
        print(f"analysis failed: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    if arguments.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(_markdown(result))


if __name__ == "__main__":
    main()
