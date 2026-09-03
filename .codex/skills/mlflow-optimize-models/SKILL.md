---
name: mlflow-optimize-models
description: Analyze and optimize ML training workflows through local or remote MLflow Tracking and Artifact APIs across TensorFlow, PyTorch, scikit-learn, boosting, Ray, and other frameworks. Use when Codex needs to inspect current or historical Runs, determine the best observed validation result, compare data-compatible experiments, diagnose training budget, overfitting, underfitting, instability, quality-gate or Artifact problems, recommend hyperparameters, modify training or tuning code, design a safe search, or validate a retrained champion without database access or test-data leakage.
---

# MLflow Model Optimization

Build an evidence-driven loop from MLflow history to parameter or code changes. Keep
the workflow experiment- and framework-agnostic; learn project-specific metric names,
data identity, model code, and release rules from the current repository and Runs.

## Establish scope

Ask for an Experiment only if it cannot be inferred from the request, environment, or
repository. Identify:

- Tracking URI and Experiment name or ID;
- optional MLflow filter for project, model family, study, or lifecycle;
- validation objective and direction;
- data identity fields or MLflow Dataset Inputs;
- final holdout metrics and promotion requirements.

Never silently use a test or holdout metric as the optimization objective.

## Route execution by scope

Choose the execution backend from the requested task scope rather than applying a universal Ray-only rule:

- Keep read-only analysis, configuration checks, bounded quick checks, and low-risk exploratory experiments local
  when they are expected to finish quickly and do not produce formal evidence.
- Prefer the project's declared Ray Job path for formal Trial/Champion runs, long-running or resource-intensive
  training, distributed work, and any result intended for durable MLflow, model, or competition evidence.
- For a project integrated with Galatea, use `galatea_plan_run` followed by `galatea_submit_job` for formal Ray
  execution. Otherwise use the project's documented immutable-release and Ray submission entrypoints.
- Before either execution path, validate the project structure, fixed entrypoint, dependencies, configuration,
  release/runtime environment, dataset identity, and split contract. A mismatch is a blocking failure: repair it
  first and never bypass it with a different command.
- A local run may inform development, but it must not be reported as a governed Ray Run, durable final evidence,
  or a replacement for a required formal submission.

## Use API-only evidence

Treat the Tracking Server as the only MLflow metadata boundary. Query Experiments,
Runs, parameters, metrics, metric history, Tags, Dataset Inputs, and Logged Models with
`MlflowClient` through the configured Tracking URI. This must work when MLflow runs on
another host and its backend database is inaccessible.

Never locate, open, copy, or query `mlflow.db`. Never fall back to a repository SQLite
file when an API request fails. For Artifacts, use MLflow Artifact APIs or the Tracking
Server proxy; do not read server filesystem paths or load object-store credentials.
Honor existing MLflow authentication and TLS environment configuration without
printing credentials.

## Analyze Runs

Resolve the directory containing this `SKILL.md` as the skill directory. Run the
bundled read-only analyzer from the target repository:

```bash
python <skill-directory>/scripts/analyze_experiment.py \
  --tracking-uri https://mlflow.example.internal \
  --experiment <experiment-name> \
  --repo-root "$PWD"
```

Add a project filter when needed:

```bash
--filter "tags.project = 'example-project'"
```

Prefer automatic validation-objective discovery only when project naming is clear.
Otherwise pass, for example:

```bash
--objective-metric best_val_accuracy --objective-mode max
```

Use repeated `--cohort-param` options when MLflow Dataset Inputs or common digest
parameters do not fully express data compatibility. Use `--format json` for downstream
automation.

Accept `--experiment-id` instead of `--experiment` when only the remote ID is known.
If a local Tracking URI fails inside a sandbox, retry read-only API access with host
permission before declaring MLflow unavailable. Inspect systemd only for an explicitly
in-scope local deployment; it is irrelevant for a remote Tracking Server.

## Interpret evidence

Separate these claims:

1. latest Run;
2. best observed compatible validation Run;
3. selected configuration that still needs clean retraining;
4. final champion that passed holdout, quality, integrity, and promotion checks.

Report objective direction, cohort identity, candidate count, parameter coverage,
generalization gap, curve summary, explicit gates, and uncertainty. Treat test/holdout
metrics as descriptive only. Never call a finite search globally optimal.

Read [references/optimization-policy.md](references/optimization-policy.md) before
recommending parameter changes, editing training code, or approving another study.

## Optimize at the smallest layer

Choose the least invasive effective action:

1. Fix tracking, data identity, leakage, failed Runs, or Artifact integrity first.
2. Change existing runtime configuration when the desired experiment is already
   supported.
3. Extend the search space when history lacks coverage but the training loop is sound.
4. Change model, data, loss, scheduler, metrics, or callback code only when evidence
   identifies a capability gap.
5. Redesign architecture or data strategy only after simpler controlled trials fail.

Do not edit code for an analysis-only request. Before any edit, inspect Git status,
preserve user changes, locate the source recorded by MLflow, and follow repository
instructions. Match the existing framework and test style.

## Preserve experiment validity

Maintain these invariants when modifying tuning code:

- select configurations on validation or cross-validation evidence only;
- evaluate the untouched holdout after selection, preferably once per champion;
- isolate Runs with incompatible data, split, preprocessing, label, or feature lineage;
- include every searched field in the stored config and stable trial identity;
- log seeds, code revision, environment, inputs, metrics, checkpoints, and model URI;
- resume studies without repeating completed configurations;
- prevent exploratory Runs from automatically changing production aliases;
- reproduce the winner from a clean source state before promotion.

## Validate changes

Run validation in increasing cost order:

1. syntax, config, and static checks;
2. focused unit tests for search-space, objective, and selection behavior;
3. a read-only plan or dry run;
4. a minimal smoke run only when requested;
5. the approved study budget only when requested;
6. analyzer rerun on the new compatible cohort.

Analysis and code optimization do not authorize a training job. Start CPU/GPU training,
register a model, or change an alias only when the user explicitly requests that state
change.

## Report outcome

Lead with a bounded verdict such as `not-ready`, `insufficient-evidence`,
`search-incomplete`, `best-observed-not-proven-optimal`, or `candidate-checks-passed`.
Include:

- exact Experiment, filter, objective, mode, and cohort identity;
- latest and best observed Run IDs;
- leaderboard and parameter-coverage evidence;
- holdout metrics clearly marked descriptive;
- ranked parameter and code actions with rationale;
- verification performed and remaining risk;
- files, Runs, registry state, or compute state changed.
