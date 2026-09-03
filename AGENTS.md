# Repository Guidelines

## Scope and Platform Architecture

This repository is a multi-project, multi-model, and multi-framework training platform. It is not
limited to image classification or to the Cats vs Dogs example. JupyterLab is the interactive
development entry point, Ray runs scheduled or distributed workloads, MLflow tracks and governs
experiments and models, and MinIO persists datasets, checkpoints, models, and other artifacts.

Keep platform concerns separate from workload concerns:

- Platform code and documentation must remain framework-neutral where practical.
- A training project may contain multiple model families, baselines, and parameter variants.
- `train-model/cats-and-dogs/` is an example workload, not the repository's architectural boundary.
- Formal training should be reproducible outside a notebook and traceable to data, code, configuration,
  environment, and an MLflow Run ID.

## Project Structure and Ownership

- `train-model/<project-name>/` contains project-specific notebooks, source, configuration, environment,
  tests, and usage documentation. New workloads belong here.
- `tests/` contains repository-level and cross-project tests. Keep project-only tests close to the project
  when that makes ownership clearer.
- `.codex/skills/` contains repository-scoped Codex workflows. `mlflow-optimize-models` is a generic
  MLflow analysis and optimization Skill, not a Cats vs Dogs-specific tuner.
- `requirements.txt` defines shared platform-service dependencies. Model and workload dependencies belong
  in `train-model/<project-name>/conda.yaml` or an equivalent project environment file.
- `doc/` contains deployment and operations guides for JupyterLab, MLflow, Ray, MinIO, and proxying.
- `systemd/` contains deployable service units. Treat users, paths, ports, hosts, and environment files as
  host-specific configuration.
- `platform-data/` contains runtime databases, object data, artifacts, and application state. It is not
  source code and must remain ignored by Git.

A mature training project should normally provide `README.md`, `configs/`, `src/`, `scripts/`, `tests/`,
and optional `notebooks/`. Existing compact projects may retain a flat layout if their training,
evaluation, and recovery paths remain explicit.

## Environment and Development Commands

Use the documented Conda installation and activate the shared environment for platform work:

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312
```

Install or update shared services from the root dependency file only when environment provisioning is in
scope. Use the project's own environment definition for model-specific work.

Start a local interactive session with:

```bash
jupyter lab --no-browser --allow-root --ServerApp.root_dir="$PWD"
```

Check the current platform before diagnosing training failures:

```bash
systemctl is-active minio.service mlflow.service jupyterlab.service
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
curl -fsS http://127.0.0.1:9000/minio/health/live
ray status
```

Validate service units before installation or after changes:

```bash
systemd-analyze verify systemd/*.service
```

## Training Project Contract

Each project must make the following behavior explicit:

- Keep data loading, model construction, training, evaluation, and promotion separable and configurable.
- Provide a parameterized non-notebook entry point for formal or long-running training.
- Record immutable dataset identity, content or manifest digest, split identity, preprocessing version,
  code revision, random seed, environment, resources, and complete hyperparameters.
- Distinguish training, validation, and final test metrics by name and meaning. Declare the primary
  objective metric and whether it is minimized or maximized.
- Use deterministic splits where possible. Never silently reshuffle an existing evaluation population.
- Store checkpoints, models, predictions, reports, and recovery metadata through the configured artifact
  service; do not treat a notebook kernel or local temporary path as durable state.
- Make retries idempotent. A retry must not overwrite another Run or publish a partially trained model.
- Require an explicit review or promotion action before changing a production model alias.

The contract applies to classification, regression, detection, segmentation, ranking, recommendation,
forecasting, fine-tuning, and other training workloads. Do not assume a particular framework, model type,
metric name, or optimization direction in shared platform code.

## MLflow Tracking and Optimization Rules

- Access local and remote MLflow deployments through Tracking, Artifact, and Model Registry APIs. Client
  code must not open, copy, or query `mlflow.db` directly.
- Take the Tracking URI and Experiment identity from explicit configuration such as
  `MLFLOW_TRACKING_URI` and `MLFLOW_EXPERIMENT_NAME`; do not assume MLflow runs on the training node.
- Compare Runs only when task, dataset version, split, preprocessing, metric definition, and evaluation
  protocol are compatible. Report when evidence is insufficient to call a result optimal.
- Select hyperparameters using training and validation evidence. Do not repeatedly use the test set for
  search, early stopping, or model selection.
- Retrain the selected configuration from a clean state, then perform the final test evaluation once and
  apply the documented quality gates before promotion.
- For distributed training, only the authoritative worker should create or finalize the parent Run and
  publish shared artifacts unless the project documents a safe nested-Run design.
- Use the MLflow Artifact API for checkpoint and model verification. Do not require clients to read the
  server's MinIO filesystem or hold the server's long-lived object-store credentials.
- Analysis or code-optimization requests do not implicitly authorize expensive CPU/GPU training or a
  Registry alias change. Run those actions only when the user requests them.

When using `.codex/skills/mlflow-optimize-models/`, choose an objective metric and `max` or `min` direction
for the project. The Skill must remain reusable across frameworks and experiments.

## Ray and Notebook Execution

- Use notebooks for exploration, visualization, single-batch checks, and short smoke tests.
- Choose execution by scope: bounded quick checks and low-risk exploratory experiments may run locally;
  formal, distributed, long-running, or resource-intensive work should prefer a parameterized Ray Job,
  Ray Train entry point, or another recoverable script-based workflow.
- If project structure, fixed entrypoint, dependencies, release, data identity, or split contract is invalid,
  block execution and repair the contract; do not bypass the failure with a local command. Local results must
  not be represented as governed Ray or final-validation evidence.
- Declare CPU, GPU, memory, and placement requirements rather than assuming all local resources are free.
- Preserve Run IDs and checkpoint locations in job metadata so failed jobs can be diagnosed or resumed.
- Seed randomized work and document any operation that cannot be made deterministic.
- Never overwrite a source notebook with executed smoke-test output; write it to a temporary directory.

## Agent SDK and Capability Alignment

- Implement Agent features against source-level SDK abstractions and Claude Code-compatible behavior where
  available. Do not invent behavior from prompts when the SDK or upstream source already defines a tool,
  permission, event, hook, or capability model.
- Support Skill capabilities as first-class runtime capabilities: discover, load, route, and execute Skills
  through the repository or configured Skill interfaces rather than hard-coding project-specific prompt text.
- Model allowlists and permission grants through the SDK permission layer. A denied or unknown action should
  be able to request approval from the console or user-facing permission flow, and users should be able to
  grant scoped access directly when the runtime supports it.
- Keep permission requests structured and reusable, including command/tool identity, scope, reason,
  persistence choice, and denial handling. Avoid one-off natural-language prompts for each new action.
- Before implementing Agent behavior, inspect the existing SDK and relevant Claude Code source-level patterns,
  then align naming, state transitions, callbacks, and error handling with those abstractions. If parity is
  impossible, document the gap and the local compatibility boundary.
- Treat prompts as policy or presentation, not as the primary implementation mechanism for capabilities,
  allowlists, or permissions.

## Coding and Documentation Style

Use four-space indentation for Python, `snake_case` for functions and variables,
`UPPER_SNAKE_CASE` for constants, and `pathlib.Path` for filesystem paths. Prefer typed, parameterized
functions over notebook-only global state. Keep framework-specific imports inside project code unless the
platform component intentionally depends on that framework.

Keep notebook cells focused and add Markdown before major stages. In Markdown, use descriptive headings,
fenced commands, relative links, and wrapped prose for readable diffs. Preserve systemd section ordering:
`[Unit]`, `[Service]`, then `[Install]`. Explain non-obvious operational or model-selection decisions with
short comments; avoid comments that merely restate the code.

## Testing and Validation

Run the narrowest relevant checks first, then broaden them in proportion to the change. The repository has
no global coverage threshold, but new reusable logic should include focused tests.

Run the current repository unit tests with:

```bash
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s tests -p 'test_*.py'
```

For project changes, follow the project's README and verify data loading, split integrity, a small training
step, evaluation semantics, MLflow logging, and artifact recovery as applicable. Run changed notebooks from
a clean kernel. Keep smoke-test budgets deliberately small and disable automatic parameter searches unless
the search itself is under test.

For the current Cats vs Dogs example, configure a non-source copy with `EPOCHS = 1` and
`RUN_AUTO_TUNING = False`, then execute it without overwriting the source notebook:

```bash
jupyter nbconvert --execute --to notebook \
  train-model/cats-and-dogs/cats-vs-dogs-classification.ipynb \
  --output-dir /tmp --output cats-vs-dogs-smoke.ipynb
```

For documentation or service changes, run applicable health checks from `doc/`, validate Markdown links,
and confirm that documented paths, ports, users, and service behavior match the repository units.

## Commit and Pull Request Guidelines

Use short, imperative, scoped subjects such as `docs: clarify MLflow artifact setup`,
`platform: add Ray job validation`, or `training: record dataset digest`. Keep generated output, datasets,
checkpoints, models, secrets, and notebook execution state out of commits.

Pull requests should explain platform and model impact, list verification performed, and identify affected
projects. Include screenshots only when notebook visualizations or web UI behavior changes. Call out changes
to ports, credentials, dependencies, storage paths, model promotion behavior, or service users prominently.
Do not stage or revert unrelated worktree changes.

## Security, Persistence, and Configuration

- Never commit tokens, passwords, object-store keys, private endpoints, or environment files containing
  secrets.
- Bind unauthenticated services to loopback where possible. Protect services listening on `0.0.0.0` with
  firewall rules, an authenticated proxy, or a controlled private network.
- Do not commit datasets, checkpoints, generated models, runtime databases, MLflow artifacts,
  `.ipynb_checkpoints/`, or Python cache files.
- Treat `platform-data/` as replaceable runtime state, not as an integration interface. Back up MLflow
  metadata and MinIO objects consistently, and test recovery before relying on them.
- Give training clients the minimum required API permissions. Keep long-lived MinIO credentials on the
  protected service side when MLflow proxies artifact access.
- Do not expose test samples, secret labels, or sensitive training examples in logs, artifacts, screenshots,
  or model-analysis responses.
