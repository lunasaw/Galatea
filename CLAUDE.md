# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Purpose

This is a **multi-project ML training platform**, not a single-model repository. The platform integrates JupyterLab + Ray + MLflow + MinIO to provide reproducible, auditable, and recoverable training workflows across multiple frameworks (TensorFlow, PyTorch, scikit-learn, XGBoost, etc.) and task types (classification, regression, detection, segmentation, ranking, recommendation, time-series, LLM fine-tuning).

The cats-and-dogs TensorFlow/Keras example in `train-model/cats-and-dogs/` is one reference workload demonstrating the platform contracts. New projects follow the same pattern but may use different frameworks, models, and objectives.

**Platform architecture**: Data → validation & versioning → Ray Job → training framework → MLflow tracking → MinIO artifact storage → Model Registry → quality gates → production deployment.

## Environment

- **Conda environment**: `attend-ray-py312` at `/data/miniconda3/envs/attend-ray-py312`
- **Working directory**: `/data/ai/chenzhangyue/galatea-model/galatea`
- **Data directory**: `/data/ai/chenzhangyue/galatea-model/train-data/` (outside the repository, not in git)
- **Platform data**: `platform-data/` (gitignored, contains runtime databases, artifacts, and state)

Activate the environment:
```bash
source /data/miniconda3/etc/profile.d/conda.sh
conda activate attend-ray-py312
```

## Platform Services

The recorded development profile runs MinIO, MLflow and the Ray Head under systemd. JupyterLab is optional. Verify the target profile before training; current machine facts are in `doc/local-services-18100.md` and `doc/platform-runtime-memory.md`.

| Service | Port | Purpose | Health Check |
| --- | ---: | --- | --- |
| JupyterLab | 8888 | Interactive development | `systemctl is-active jupyterlab.service` |
| MLflow Tracking | 18103 | Experiment tracking & artifact proxy | `curl -fsS http://127.0.0.1:18103/health` |
| MinIO API | 18101 | S3-compatible object storage | `curl -fsS http://127.0.0.1:18101/minio/health/live` |
| MinIO Console | 18102 | Storage management UI | Web access |
| Ray Head / Jobs | 18104 / 8265 | Governed job execution & dashboard | `ray status --address=10.26.0.140:18104` |

The development Ray Head is managed by `ray-head.service`; select explicit GCS and Jobs API addresses before submitting any Training Run (see `doc/ray-start.md`). Training clients only access MLflow API at `http://127.0.0.1:18103` and never read `platform-data/mlflow/mlflow.db` directly. MLflow Server proxies all artifact writes to MinIO using credentials from `/etc/minio/mlflow-s3.env` (never commit this file).

## Development Commands

**Start JupyterLab** (if not running as systemd service):
```bash
jupyter lab --no-browser --allow-root --ServerApp.root_dir="$PWD"
```

**Run tests**:
```bash
/data/miniconda3/envs/attend-ray-py312/bin/python -m unittest discover \
  -s tests -p 'test_*.py'
/data/miniconda3/envs/attend-ray-py312/bin/python -m unittest discover \
  -s train-model/cats-and-dogs/tests -p 'test_*.py'
/data/miniconda3/envs/attend-ray-py312/bin/python -m unittest discover \
  -s train-model/ray-cats-and-dogs/tests -p 'test_*.py'
/data/miniconda3/envs/attend-ray-py312/bin/python -m unittest discover \
  -s train-model/ray-handwritten-digits/tests -p 'test_*.py'
```

**Validate systemd units** before deployment:
```bash
systemd-analyze verify systemd/*.service
```

**Notebook validation**: Local checks must be read-only or forward-only. Any parameter fitting,
optimizer update, checkpoint or durable training evidence must use the project's declared governed
backend, even for a one-epoch smoke. Use the owning project README and fixed entrypoint; keep permitted
notebook outputs in a temporary directory without overwriting the source.

**Analyze MLflow experiments** (framework-agnostic):
```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:18103
export MLFLOW_EXPERIMENT_NAME="your-experiment-name"
python .codex/skills/mlflow-optimize-models/scripts/analyze_experiment.py \
  --tracking-uri "$MLFLOW_TRACKING_URI" \
  --experiment "$MLFLOW_EXPERIMENT_NAME" \
  --objective-metric your_val_metric \
  --objective-mode max \
  --repo-root "$PWD"
```

**Codex and platform checks**:
```bash
cd packages/galatea-governance
npm ci
npm test
npm run typecheck
npm run build
```

Run the Python Host and platform suites from the repository root:
```bash
python -m unittest discover -s agents/codex-app-server/tests -p 'test_*.py'
PYTHONPATH=services/galatea-mcp/src \
  python -m unittest discover -s services/galatea-mcp/tests -p 'test_*.py'
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

mkdir -p /data/ai/chenzhangyue/galatea-model/train-data/cats-and-dogs
kaggle datasets download \
  -d shaunthesheep/microsoft-catsvsdogs-dataset \
  -p /data/ai/chenzhangyue/galatea-model/train-data/cats-and-dogs \
  --force

unzip -q /data/ai/chenzhangyue/galatea-model/train-data/cats-and-dogs/microsoft-catsvsdogs-dataset.zip \
  -d /data/ai/chenzhangyue/galatea-model/train-data/cats-and-dogs

# Verify: should both output 12500
find /data/ai/chenzhangyue/galatea-model/train-data/cats-and-dogs/PetImages/Cat -maxdepth 1 -type f -iname '*.jpg' | wc -l
find /data/ai/chenzhangyue/galatea-model/train-data/cats-and-dogs/PetImages/Dog -maxdepth 1 -type f -iname '*.jpg' | wc -l
```

**First-time validation**: Use read-only configuration/data checks and forward-only fixtures. Training smoke runs still use the fixed governed backend with an explicit budget. Known corrupted images (Cat/666.jpg, Dog/11702.jpg) are handled by the workload data contract.

**Configure MLflow client**:
```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:18103
export MLFLOW_EXPERIMENT_NAME=your-experiment-name
```

For remote MLflow servers, use the HTTPS tracking URI and authentication config. Never copy server database or MinIO credentials to training projects.

## Codex Integration

Codex is the sole Agent Runtime. Repository workflows use `.codex/skills/` and fixed project entrypoints.
The Web Agent in `agents/codex-app-server/` hosts the native `codex app-server` over stdio JSON-RPC;
Codex owns Thread, Turn, Skill, sandbox, approval and the Agent Loop. The generic Host provides an
HTTP/SSE Console, process/session recovery and an optional Dynamic Tool Registry backed by configured
Python Providers. It has no Galatea service dependency, built-in business adapter or training state machine.
The Console uses one Host identity; protect external access with an authenticated proxy or controlled network.

`services/galatea-mcp/` owns Campaign scope, budgets, idempotent operations, final-test usage and platform
API evidence. Connect its independent MCP transport through native `codex-home/config.toml`; configure
Skill roots separately in `agent.json`. Never run two writers against the same business state. Native
approval cannot grant platform business authorization. Production startup requires an accepted release
and explicit model configuration; documentation or protocol tests do not establish training acceptance.

`packages/galatea-governance/` contains runtime-independent policies used by fixed project-YAML entrypoints
such as `train-model/ray-cats-and-dogs/job/governed.mjs`. It owns no Agent Loop, Session or model client.
Plans and submissions still require immutable releases, explicit cluster selection and evidence-bound
authorization. A successful Ray Job does not imply quality approval or permission to change a production alias.

Only `codex-agent/v2` Host configuration is accepted. Old principal/galatea fields, state and release
acceptance records are not automatically migrated. The removed Python Runner is not a deployment entrypoint;
the published training Skill bundle still has a historical `wait_external` contract that the generic Host
does not schedule. Business integration and training-stage/artifact visualization require separate acceptance.

After source, configuration, entrypoint, dependency or data-identity changes, build a new immutable Release;
old releases never absorb workspace edits. Use the actual source/protocol surfaces for Agent features and
keep credentials in protected host configuration, outside model input, subprocess environments and logs.

See `doc/agent-galatea.md` and `agents/codex-app-server/docs/06-config-deployment.md` for boundaries and deployment.

## Documentation

- [Current machine deployment](doc/local-services-18100.md)
- [Cluster profiles and runtime memory](doc/platform-runtime-memory.md)
- [JupyterLab deployment](doc/jupyter-start.md)
- [MLflow Tracking Server deployment](doc/mlflow-start.md)
- [MinIO deployment](doc/minio-start.md)
- [Ray deployment and job submission](doc/ray-start.md)
- [code-server proxy configuration](doc/code-server-proxy.md)
- [End-to-end implementation guide](doc/train-guide/data-to-training-to-model-imp-guide.md)
- [Repository development conventions](AGENTS.md)
- [Platform overview and architecture](README.md)
- [Codex and Galatea architecture](doc/agent-galatea.md)
- [Codex app-server deployment](agents/codex-app-server/docs/06-config-deployment.md)
