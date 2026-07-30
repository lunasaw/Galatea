# Repository Guidelines

## Project Structure & Module Organization

- `cats-and-dogs/` contains the TensorFlow/Keras image-classification notebook.
- `doc/` contains deployment and operations guides for JupyterLab, MLflow, Ray, MinIO, and code-server proxying.
- `systemd/` contains deployable service units. Treat paths, users, ports, and environment files as host-specific.
- `platform-data/` holds runtime databases, artifacts, and application state and is intentionally ignored by Git. Do not commit datasets, checkpoints, generated models, secrets, or `.ipynb_checkpoints/`.

## Build, Test, and Development Commands

This repository has no package manifest or build step. Use the documented Conda environment:

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312
jupyter lab --no-browser --allow-root --ServerApp.root_dir="$PWD"
```

Validate service files before installing them:

```bash
systemd-analyze verify systemd/*.service
```

For a full notebook check, ensure the dataset exists at the configured path, set `EPOCHS = 1`, then run:

```bash
jupyter nbconvert --execute --to notebook \
  cats-and-dogs/cats-vs-dogs-classification.ipynb \
  --output-dir /tmp --output cats-vs-dogs-smoke.ipynb
```

## Coding Style & Naming Conventions

Use four-space indentation for Python, `snake_case` for functions and variables, `UPPER_SNAKE_CASE` for constants, and `pathlib.Path` for filesystem paths. Keep notebook cells focused, add Markdown before major stages, and seed randomized experiments. In Markdown, use descriptive headings, fenced commands, relative links, and wrap long prose for readable diffs. Preserve systemd section ordering: `[Unit]`, `[Service]`, then `[Install]`.

## Testing Guidelines

No automated test suite or coverage threshold is currently configured. Run changed notebook cells from a clean kernel and verify data splitting, training, evaluation, and plots. Never overwrite the source notebook with smoke-test output. For documentation or service changes, execute applicable health checks from `doc/` and confirm referenced paths and ports match the unit files.

## Commit & Pull Request Guidelines

The repository has no commits yet, so no historical message convention exists. Use short, imperative, scoped subjects such as `docs: clarify MLflow artifact setup` or `notebook: add validation split`. Keep generated output out of commits. Pull requests should explain the operational or model impact, list verification performed, link relevant issues, and include screenshots only when notebook visualizations or web UI behavior changes. Call out changes to ports, credentials, storage paths, or service users prominently.

## Security & Configuration

Never commit tokens, object-storage keys, or environment files. Bind unauthenticated services to loopback where possible; if a service listens on `0.0.0.0`, require firewall protection and an authenticated proxy as described in `doc/`.
