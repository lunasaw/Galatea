# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Purpose

This is a machine learning operations repository for training and tracking models. It provides:
- Image classification notebooks (TensorFlow/Keras)
- MLflow tracking server for experiment management
- MinIO object storage for artifacts
- JupyterLab development environment
- Systemd service configurations for production deployment

The architecture follows a reproducible ML pipeline: data → training (Ray Job) → PyTorch model → MLflow tracking → model registry → production deployment.

## Environment

- **Conda environment**: `attend-ray-py312` at `/data/conda/envs/attend-ray-py312`
- **Working directory**: `/data/ai/chenzhangyue/code/train`
- **Data directory**: `/data/ai/chenzhangyue/code/data/` (sibling to train, not in git)
- **Platform data**: `platform-data/` (gitignored, contains runtime databases, artifacts, and state)

Activate the environment:
```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312
```

## Development Commands

**Start JupyterLab** (if not already running as a service):
```bash
jupyter lab --no-browser --allow-root --ServerApp.root_dir="$PWD"
```

**Validate systemd service files** before deployment:
```bash
systemd-analyze verify systemd/*.service
```

**Quick notebook smoke test** (cats-and-dogs example):
```bash
# First, edit the notebook to set EPOCHS = 1
jupyter nbconvert --execute --to notebook \
  cats-and-dogs/cats-vs-dogs-classification.ipynb \
  --output-dir /tmp --output cats-vs-dogs-smoke.ipynb
```

## Architecture Overview

**Directory structure:**
- `cats-and-dogs/` — TensorFlow/Keras image classification notebook
- `doc/` — Deployment guides for JupyterLab, MLflow, Ray, MinIO, and code-server proxy
- `systemd/` — Service unit files (paths, users, ports are host-specific)
- `platform-data/` — Runtime state (databases, artifacts, checkpoints, secrets) - **never commit**

**Service dependencies:**
- MLflow depends on MinIO (for S3-compatible artifact storage)
- JupyterLab runs independently on port 8888
- MLflow tracking server runs on port 5000
- MinIO runs on port 9000 (API) and 9001 (console)

**Data flow for cats-and-dogs notebook:**
1. Dataset at `/data/ai/chenzhangyue/code/data/cats-and-dogs/PetImages/` (Cat/ and Dog/)
2. Notebook verifies images and skips corrupted ones (Cat/666.jpg, Dog/11702.jpg)
3. Splits data: 90% train, 5% validation, 5% test (fixed random seed)
4. Creates temporary working copy in `/tmp/cats-v-dogs`
5. Trains CNN models with configurable EPOCHS variable
6. Generates training curves, predictions, and Grad-CAM visualizations

## Key Configuration Files

**systemd service files** follow this pattern:
- User/Group: `root` (host-specific)
- Environment: Uses `attend-ray-py312` conda environment
- Working directory: `/data/ai/chenzhangyue/code/train`
- MLflow uses environment file at `/etc/minio/mlflow-s3.env` for S3 credentials

**MLflow configuration:**
- Backend store: SQLite at `platform-data/mlflow/mlflow.db`
- Artifact store: S3-compatible MinIO bucket `mlflow-artifacts`
- Credentials: Never commit; stored in `/etc/minio/mlflow-s3.env`

## Code Style

- **Python**: 4-space indentation, `snake_case` for functions/variables, `UPPER_SNAKE_CASE` for constants
- **Paths**: Use `pathlib.Path` for filesystem operations
- **Notebooks**: Focused cells, Markdown before major stages, seed all randomized experiments
- **Markdown**: Descriptive headings, fenced code blocks, relative links, wrap prose for readable diffs
- **Systemd**: Preserve section order: `[Unit]`, `[Service]`, `[Install]`

## Testing and Verification

No automated test suite exists. For notebook changes:
1. Run changed cells from a clean kernel
2. Verify data splitting, training, evaluation, and visualization
3. Never overwrite source notebooks with smoke-test output

For documentation or service changes:
1. Execute health checks from `doc/` directory
2. Confirm paths and ports match systemd unit files
3. Verify service dependencies (e.g., MLflow after MinIO)

## Commits and PRs

Use imperative, scoped commit messages:
- `docs: clarify MLflow artifact setup`
- `notebook: add validation split`
- `systemd: update JupyterLab base URL`

PR requirements:
- Explain operational or model impact
- List verification performed
- Call out changes to ports, credentials, storage paths, or service users
- Include screenshots only for notebook visualizations or web UI changes

## Security

- Never commit tokens, object-storage keys, or environment files
- Bind unauthenticated services to loopback where possible
- Services listening on `0.0.0.0` require firewall protection and authenticated proxy (see `doc/code-server-proxy.md`)
- Platform data directory (`platform-data/`) is gitignored for this reason

## Common Tasks

**Setting up cats-and-dogs dataset:**
```bash
# Install Kaggle CLI
python -m pip install kaggle

# Download dataset (requires ~/.kaggle/kaggle.json)
mkdir -p /data/ai/chenzhangyue/code/data/cats-and-dogs
kaggle datasets download \
  -d shaunthesheep/microsoft-catsvsdogs-dataset \
  -p /data/ai/chenzhangyue/code/data/cats-and-dogs \
  --force

# Extract
unzip -q /data/ai/chenzhangyue/code/data/cats-and-dogs/microsoft-catsvsdogs-dataset.zip \
  -d /data/ai/chenzhangyue/code/data/cats-and-dogs

# Verify counts (should both output 12500)
find /data/ai/chenzhangyue/code/data/cats-and-dogs/PetImages/Cat -maxdepth 1 -type f -iname '*.jpg' | wc -l
find /data/ai/chenzhangyue/code/data/cats-and-dogs/PetImages/Dog -maxdepth 1 -type f -iname '*.jpg' | wc -l
```

**For first-time notebook runs:** Set `EPOCHS = 1` in the import cell to verify the pipeline, then change back to `10` for full training.
