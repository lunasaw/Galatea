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
python -m unittest tests/test_cats_dogs_tuner.py
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

**Agent system commands**:
```bash
# Test agent tools directly (no API calls)
python agent/test/test_tools_direct.py

# Run agent demos (requires ANTHROPIC_API_KEY)
python agent/demo/demo_basic.py
python agent/demo/demo_quick.py

# Test configuration loading
python agent/test/test_config.py
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

Run tests with `python -m unittest tests/<test_file>.py`. For notebook changes, run modified cells from a clean kernel and verify data splitting, training, evaluation, and visualization. Never overwrite source notebooks with smoke-test output.

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

## Agent System

The `agent/` directory contains a Python-based agent orchestration system built on Claude Agent SDK with custom MCP tools for platform automation.

### Purpose

Automate ML platform operations through structured, auditable agent workflows:
- Platform inspection (service health, project structure, experiment analysis)
- Data preparation with Ray Data (future)
- Training job orchestration (future)
- Model evaluation and promotion (future)

### Directory Structure

```
agent/
├── runtime.py              # GalateaRuntime - Claude SDK wrapper
├── client.py               # High-level client for common operations
├── tools/                  # MCP tool implementations
│   ├── server.py           # MCP server factory with @tool decorators
│   └── inspection.py       # Read-only platform inspection tools
├── schemas/                # Pydantic models for structured output
│   ├── common.py           # StageResult, ArtifactRef, Evidence
│   └── inspection.py       # InspectionResult models
├── config/                 # Configuration loading and validation
│   └── loader.py           # Load ANTHROPIC_API_KEY from ~/.claude/settings.json
├── demo/                   # Demo scripts (requires ANTHROPIC_API_KEY)
│   ├── demo_basic.py       # Full platform inspection demo
│   └── demo_quick.py       # Quick tool demonstration
├── test/                   # Test scripts
│   ├── test_tools_direct.py   # Direct tool testing (no API calls)
│   └── test_config.py      # Configuration loading tests
├── summary/                # Implementation reports and completion records
├── doc/                    # Architecture documentation
├── agents/                 # Agent definitions (future: trainer, tuner, etc.)
├── workflows/              # Multi-stage workflow orchestration (future)
├── state/                  # Session and experiment state management (future)
├── hooks/                  # Permission and policy hooks (future)
└── scripts/                # CLI entry points (future)
```

### Organization Conventions

When working in `agent/`:

1. **Test files belong in `agent/test/`**: All test scripts use the pattern `test_*.py` and go in the `test/` directory
2. **Demo files belong in `agent/demo/`**: All demonstration scripts use the pattern `demo_*.py` and go in the `demo/` directory
3. **Core modules stay at top level**: Only `runtime.py`, `client.py`, `__init__.py` at the top level
4. **Functional grouping in subdirectories**: Tools, schemas, configs, workflows each have dedicated directories
5. **Documentation in `doc/`**: Architecture, implementation guides, and design documents
6. **Summary reports in `summary/`**: Stage completion reports and implementation summaries

Never place test or demo files directly in `agent/` root. Use the appropriate subdirectory.

### Key Commands

```bash
# Direct tool testing (no API calls, fast)
python agent/test/test_tools_direct.py

# Configuration testing
python agent/test/test_config.py

# Full agent demo (requires ANTHROPIC_API_KEY in env or ~/.claude/settings.json)
python agent/demo/demo_basic.py

# Quick demo
python agent/demo/demo_quick.py
```

### API Configuration

The agent runtime requires Anthropic API credentials. Configure via `~/.claude/settings.json` (recommended) or environment variables:

```json
# ~/.claude/settings.json
{
  "env": {
    "ANTHROPIC_API_KEY": "your-api-key",
    "ANTHROPIC_BASE_URL": "https://ai.vdian.net/api/"  # Optional: custom endpoint
  }
}
```

Or environment variables:
```bash
export ANTHROPIC_API_KEY="your-api-key"
export ANTHROPIC_BASE_URL="https://your-endpoint/api/"  # Optional
```

### Integration with Platform

The agent system follows platform contracts:
- Uses MLflow Tracking API only (never reads `mlflow.db` directly)
- Submits Ray jobs through Ray Jobs API
- Accesses artifacts through MLflow Artifact API (proxied to MinIO)
- All operations are idempotent and auditable
- Read-only by default; destructive actions require explicit approval

### Current Status

**Stage 1 Complete** (Read-only Runtime POC):
- ✅ Claude SDK integration with in-process MCP server
- ✅ 5 inspection tools: list projects, inspect structure, check services, MLflow/Ray status
- ✅ Async context manager pattern with `GalateaRuntime`
- ✅ Configuration auto-loading from `~/.claude/settings.json`
- ✅ Structured schemas with Pydantic validation

**Future Stages**: DataAgent (Ray Data workflows), TrainingAgent (job orchestration), InferenceAgent (serving), approval workflows, code maintenance.

See `agent/README.md` for complete documentation, architecture details, and usage examples.

## Documentation

- [JupyterLab deployment](doc/jupyter-start.md)
- [MLflow Tracking Server deployment](doc/mlflow-start.md)
- [MinIO deployment](doc/minio-start.md)
- [Ray deployment and job submission](doc/ray-start.md)
- [code-server proxy configuration](doc/code-server-proxy.md)
- [End-to-end implementation guide](doc/data-to-training-to-model-imp-guide.md)
- [Repository development conventions](AGENTS.md)
- [Platform overview and architecture](README.md)
- [Agent system documentation](agent/README.md)
- [Agent architecture](agent/doc/current-agent-architecture.md)
