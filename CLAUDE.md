# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Purpose

This is a **multi-project ML training platform**, not a single-model repository. The platform integrates JupyterLab + Ray + MLflow + MinIO to provide reproducible, auditable, and recoverable training workflows across multiple frameworks (TensorFlow, PyTorch, scikit-learn, XGBoost, etc.) and task types (classification, regression, detection, segmentation, ranking, recommendation, time-series, LLM fine-tuning).

The cats-and-dogs TensorFlow/Keras example in `train-model/cats-and-dogs/` is one reference workload demonstrating the platform contracts. New projects follow the same pattern but may use different frameworks, models, and objectives.

**Platform architecture**: Data → validation & versioning → Ray Job → training framework → MLflow tracking → MinIO artifact storage → Model Registry → quality gates → production deployment.

## Environment

- **Conda environment**: `attend-ray-py312` at `/data/conda/envs/attend-ray-py312`
- **Working directory**: `/data/ai/chenzhangyue/code/galatea`
- **Data directory**: `/data/ai/chenzhangyue/code/data/` (sibling to train, not in git)
- **Platform data**: `platform-data/` (gitignored, contains runtime databases, artifacts, and state)

Activate the environment:
```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312
```

## Platform Services

All services are managed by systemd. Required before training:

| Service | Port | Purpose | Health Check |
| --- | ---: | --- | --- |
| JupyterLab | 8888 | Interactive development | `systemctl is-active jupyterlab.service` |
| MLflow Tracking | 5000 | Experiment tracking & artifact proxy | `curl -fsS http://127.0.0.1:5000/health` |
| MinIO API | 9000 | S3-compatible object storage | `curl -fsS http://127.0.0.1:9000/minio/health/live` |
| MinIO Console | 9001 | Storage management UI | Web access |
| Ray (optional) | 8265 | Distributed job execution & dashboard | `ray status` |

Ray Head is started on-demand before submitting formal training jobs (see `doc/ray-start.md`). Training clients only access MLflow API at `http://127.0.0.1:5000` and never read `platform-data/mlflow/mlflow.db` directly. MLflow Server proxies all artifact writes to MinIO using credentials from `/etc/minio/mlflow-s3.env` (never commit this file).

## Development Commands

**Start JupyterLab** (if not running as systemd service):
```bash
jupyter lab --no-browser --allow-root --ServerApp.root_dir="$PWD"
```

**Run tests**:
```bash
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s tests -p 'test_*.py'
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s train-model/cats-and-dogs/tests -p 'test_*.py'
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s train-model/ray-cats-and-dogs/tests -p 'test_*.py'
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s train-model/ray-handwritten-digits/tests -p 'test_*.py'
```

**Validate systemd units** before deployment:
```bash
systemd-analyze verify systemd/*.service
```

**Notebook smoke test** (cats-and-dogs example):
```bash
# Edit notebook to set EPOCHS=1, RUN_AUTO_TUNING=False first
jupyter nbconvert --execute --to notebook \
  train-model/cats-and-dogs/cats-vs-dogs-classification.ipynb \
  --output-dir /tmp --output cats-vs-dogs-smoke.ipynb
```

**Analyze MLflow experiments** (framework-agnostic):
```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
export MLFLOW_EXPERIMENT_NAME="your-experiment-name"
python .codex/skills/mlflow-optimize-models/scripts/analyze_experiment.py \
  --tracking-uri "$MLFLOW_TRACKING_URI" \
  --experiment "$MLFLOW_EXPERIMENT_NAME" \
  --objective-metric your_val_metric \
  --objective-mode max \
  --repo-root "$PWD"
```

**dsh-galatea plugin checks**:
```bash
cd plugins/dsh-galatea
node --test tests/*.test.ts
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/tsc -p tsconfig.build.json

# Run source-level Harness integration tests from the neighboring checkout
/data/ai/chenzhangyue/code/deepseek-harness/node_modules/.bin/vitest run \
  --config "$PWD/vitest.harness.config.ts"
```

## Platform Contracts

Every training project must satisfy these requirements to integrate with the platform:

1. **API-only MLflow access**: Use MLflow Tracking/Artifact APIs only. Never read `mlflow.db` or assume filesystem artifact paths.
2. **Data versioning**: Record data source URI, content digest, split digest, and preprocessing version in MLflow Dataset Inputs or tags.
3. **Run metadata**: Log project name, model variant, code version, seed, resources, and complete hyperparameters for every Run.
4. **Metric semantics**: Clearly separate training, validation, and final test metrics. Never optimize hyperparameters on test data.
5. **Artifact persistence**: Store checkpoints, models, predictions, and reports through MLflow Artifact API (proxied to MinIO).
6. **Idempotent retries**: Ray retries must not corrupt or duplicate MLflow Runs. Only one authoritative process writes per Run.
7. **No automatic promotion**: Exploratory runs must not modify production model aliases. Registration requires explicit approval.
8. **Git hygiene**: Never commit credentials, datasets, checkpoints, generated models, or `.ipynb_checkpoints/`.

Formal training should use parameterized scripts submitted as Ray Jobs. Notebooks are for development and visualization, not long-running production training.

## Project Structure for New Training Work

Place new projects under `train-model/<project-name>/`. One project may contain multiple models, algorithms, and parameter variants. Recommended structure:

```text
train-model/<project-name>/
├── README.md                 # Data sources, objective metrics, run instructions, quality gates
├── conda.yaml                # Reproducible environment (project-specific dependencies only)
├── notebooks/                # Exploration and smoke tests
├── configs/                  # Development, tuning, and production configurations
├── src/                      # Data, model, training, evaluation, and registration logic
├── scripts/                  # validate/train/evaluate/promote entry points
└── tests/                    # Data, model, selection logic, and smoke tests
```

Lighter flat structures are acceptable if they meet platform contracts. See `train-model/cats-and-dogs/` for a working example.

## MLflow Optimization Skill

The repository includes a framework-agnostic skill at `.codex/skills/mlflow-optimize-models/` that analyzes MLflow experiments and recommends parameter or code changes. It works across TensorFlow, PyTorch, scikit-learn, boosting, Ray Train, and other MLflow-compatible frameworks. It never accesses `mlflow.db` and does not use test metrics for hyperparameter selection by default.

Use it when you need to:
- Find the best observed validation result in an experiment
- Compare data-compatible runs
- Diagnose training budget, overfitting, underfitting, or instability
- Recommend hyperparameters or training code changes
- Design safe search spaces or validate retrained champions

Analysis does not automatically start GPU training. Only run training when explicitly requested.

## Code Style

- **Python**: 4-space indentation, `snake_case` for functions/variables, `UPPER_SNAKE_CASE` for constants
- **Comments**: All code comments, docstrings, and inline explanations must be written in Chinese (中文)
- **Paths**: Use `pathlib.Path` for filesystem operations
- **Notebooks**: Focused cells, Markdown before major stages, seed all randomized experiments
- **Markdown**: Descriptive headings, fenced code blocks, relative links, wrap prose for readable diffs
- **Systemd**: Preserve section order: `[Unit]`, `[Service]`, `[Install]`

## Testing and Verification

Run repository-level tests with `python -m unittest discover -s tests -p 'test_*.py'`, and
project-specific tests from the owning `train-model/<project-name>/tests/` directory. For notebook
changes, run modified cells from a clean kernel and verify data splitting, training, evaluation, and
visualization. Never overwrite source notebooks with smoke-test output.

For service or documentation changes, execute health checks from `doc/` and confirm paths/ports match systemd unit files. Verify service dependencies (MLflow requires MinIO).

## Commits and PRs

Use imperative, scoped commit messages:
- `docs: clarify MLflow artifact setup`
- `notebook: add validation split`
- `systemd: update JupyterLab base URL`
- `feat: add PyTorch training pipeline`
- `fix: prevent duplicate MLflow runs on retry`

PR requirements:
- Explain operational or model impact
- List verification performed (tests, smoke runs, health checks)
- Call out changes to ports, credentials, storage paths, or service users
- Include screenshots only for notebook visualizations or web UI changes

## Security

- Never commit tokens, object-storage keys, or environment files (especially `/etc/minio/mlflow-s3.env`)
- Bind unauthenticated services to loopback where possible
- Services listening on `0.0.0.0` require firewall protection and authenticated proxy (see `doc/code-server-proxy.md`)
- Platform data directory (`platform-data/`) is gitignored and contains sensitive runtime state
- Training clients receive minimum required permissions; MinIO long-term keys stay in protected server environment files

## Common Tasks

**Setting up cats-and-dogs dataset**:
```bash
python -m pip install kaggle  # Requires ~/.kaggle/kaggle.json

mkdir -p /data/ai/chenzhangyue/code/data/cats-and-dogs
kaggle datasets download \
  -d shaunthesheep/microsoft-catsvsdogs-dataset \
  -p /data/ai/chenzhangyue/code/data/cats-and-dogs \
  --force

unzip -q /data/ai/chenzhangyue/code/data/cats-and-dogs/microsoft-catsvsdogs-dataset.zip \
  -d /data/ai/chenzhangyue/code/data/cats-and-dogs

# Verify: should both output 12500
find /data/ai/chenzhangyue/code/data/cats-and-dogs/PetImages/Cat -maxdepth 1 -type f -iname '*.jpg' | wc -l
find /data/ai/chenzhangyue/code/data/cats-and-dogs/PetImages/Dog -maxdepth 1 -type f -iname '*.jpg' | wc -l
```

**First-time notebook runs**: Set `EPOCHS = 1` in the parameters cell to verify the pipeline, then change to `10` for full training. Known corrupted images (Cat/666.jpg, Dog/11702.jpg) are automatically skipped.

**Configure MLflow client**:
```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
export MLFLOW_EXPERIMENT_NAME=your-experiment-name
```

For remote MLflow servers, use the HTTPS tracking URI and authentication config. Never copy server database or MinIO credentials to training projects.

## Harness Integration

DeepSeek Harness is the repository's only Agent Runtime. The TypeScript ESM package in
`plugins/dsh-galatea/` registers 14 typed Cordis Tools for administrator-configured project listing and
Session-scoped selection, project inspection/configuration, Ray Job lifecycle, MLflow evidence, stage
approval, and explicitly approved model promotion. The current bundle registers `ray-cats-and-dogs`
and `ray-handwritten-digits`, defaulting to the former.

The plugin does not own an Agent Loop, Session, Workflow, permission system, Skill Registry, CLI, or
model client. Successful project-selection Tool events are replayed through the Harness
`galateaProjectSelection` Session projection; selection is not a process-global singleton and can only
route to registry entries configured by an administrator. This trusted-project routing is not tenant
isolation: shared Ray/MLflow clients and credentials remain a common trust domain. Its project entrypoints
are fixed argv arrays declared in `galatea.project.yaml`; arbitrary shell commands are not model-facing
capabilities. `configPath` is project-relative below the declared `configRoot`, while
`releaseManifestPath` is relative to that project's configured immutable `releaseRoot`.

Lifecycle/evidence results with `operationStatus` report execution, quality, governance, and
preprocessing/migration integrity independently. Readiness fails closed when required integrity evidence
is absent, unknown, inapplicable for an applicable role, or failed. A Ray success never implies quality or
authorization. Promotion is never automatic and always requires an explicit `galatea_promote_model` call with
current final-validation evidence. Harness `danger-full-access` authorizes governed actions without a prompt;
other permission presets require one-time approval. With approval policy `never` outside full access, governed
submit, resume, and promotion fail closed; retrying cannot bypass disabled prompts.

Credentials are injected by the Harness process. Plugin configuration stores only the name of an
environment variable containing a bearer token, never the token itself. Missing referenced variables
fail startup rather than silently falling back to unauthenticated access. Project subprocesses inherit only
the configured environment allowlist (a small non-secret default), not the full Harness environment.
Ray log reads use a character-offset cursor: feed each `nextLogCursor` into the next `logCursor`, handle
truncation/reset flags, and prefer status-only observations after the first log read.

The plugin consumes and binds declarations from immutable release manifests but does not build or overwrite
runtime packages. After source, entrypoint, dependency/packaging, or data/split-identity changes, rebuild and
publish a new release and plan against its new relative `<release-id>/release.json`; old releases do not
absorb workspace changes.

See `plugins/dsh-galatea/README.md` for development commands and `doc/dsh-galatea-operations.md` for
Profile installation, deployment configuration, release handling, and operational recovery.

## Documentation

- [JupyterLab deployment](doc/jupyter-start.md)
- [MLflow Tracking Server deployment](doc/mlflow-start.md)
- [MinIO deployment](doc/minio-start.md)
- [Ray deployment and job submission](doc/ray-start.md)
- [code-server proxy configuration](doc/code-server-proxy.md)
- [End-to-end implementation guide](doc/train-guide/data-to-training-to-model-imp-guide.md)
- [Repository development conventions](AGENTS.md)
- [Platform overview and architecture](README.md)
- [DeepSeek Harness and Galatea architecture](doc/agent-galatea.md)
- [dsh-galatea operations](doc/dsh-galatea-operations.md)
