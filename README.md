# ML Training Platform

Machine learning operations repository for reproducible model training, experiment tracking, and deployment.

## Overview

This repository provides a complete ML infrastructure stack:

- **JupyterLab** — Interactive development environment
- **MLflow** — Experiment tracking and model registry
- **MinIO** — S3-compatible object storage for artifacts
- **Ray** — Distributed training job scheduling
- **Systemd services** — Production deployment configurations

## Quick Start

Activate the environment and start JupyterLab:

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312
jupyter lab --no-browser --allow-root --ServerApp.root_dir="$PWD"
```

## Projects

### Cats and Dogs Classification

TensorFlow/Keras image classification example using the Microsoft Cats vs Dogs dataset.

- **Notebook**: `cats-and-dogs/cats-vs-dogs-classification.ipynb`
- **Dataset**: Microsoft Cats vs Dogs (25,000 images)
- **Models**: Baseline CNN and data-augmented CNN
- **Features**: Training curves, prediction visualization, Grad-CAM

See [`cats-and-dogs/README.md`](cats-and-dogs/README.md) for setup instructions.

## Documentation

- **[CLAUDE.md](CLAUDE.md)** — Development guide for Claude Code
- **[AGENTS.md](AGENTS.md)** — Repository guidelines and conventions
- **[doc/](doc/)** — Service deployment and operations guides
  - [JupyterLab setup](doc/jupyter-start.md)
  - [MLflow setup](doc/mlflow-start.md)
  - [MinIO setup](doc/minio-start.md)
  - [Ray setup](doc/ray-start.md)
  - [Code-server proxy](doc/code-server-proxy.md)
  - [End-to-end implementation guide](doc/data-to-training-to-model-implementation-guide.md)

## Architecture

```text
Data → Validation → Train/Val/Test Split → Ray Job
  ↓
PyTorch Training
  ├── Metrics → MLflow Tracking
  ├── Checkpoints → MinIO
  └── Logs → Ray
  ↓
Model Evaluation → MLflow Model Registry → Production
```

## Repository Structure

```
train/
├── cats-and-dogs/          # Image classification notebook
├── doc/                    # Deployment and operations guides
├── systemd/                # Service unit files
├── platform-data/          # Runtime state (gitignored)
│   ├── jupyter/           # JupyterLab config and data
│   ├── mlflow/            # MLflow tracking database
│   └── minio/             # MinIO object storage
├── CLAUDE.md              # Claude Code development guide
├── AGENTS.md              # Repository guidelines
└── README.md              # This file
```

## Services

Start services using systemd:

```bash
# Validate service files
systemd-analyze verify systemd/*.service

# Install and start
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now minio jupyterlab mlflow
```

**Service dependencies:**
- MLflow depends on MinIO (for artifact storage)
- All services use the `attend-ray-py312` conda environment

**Default ports:**
- JupyterLab: 8888
- MLflow: 5000
- MinIO API: 9000
- MinIO Console: 9001

## Development

See [CLAUDE.md](CLAUDE.md) and [AGENTS.md](AGENTS.md) for detailed development guidelines.

**Quick validation:**
```bash
# Smoke test a notebook
jupyter nbconvert --execute --to notebook \
  cats-and-dogs/cats-vs-dogs-classification.ipynb \
  --output-dir /tmp --output cats-vs-dogs-smoke.ipynb
```

## Security

- Never commit credentials, tokens, or environment files
- `platform-data/` is gitignored (contains runtime state and secrets)
- See `doc/code-server-proxy.md` for authenticated proxy setup
