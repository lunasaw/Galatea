---
name: model-project-structure
description: Enforce the repository layout for model and training projects under train-model. Use when creating, scaffolding, reorganizing, moving, reviewing, or adding code, YAML configuration, tests, scripts, notebooks, or environments for any model project; use also when deciding whether a test belongs in the repository-level tests directory or inside a model project.
---

# Model Project Structure

Keep every workload self-contained under one immediate child of `train-model/`. Separate project-owned configuration, implementation, tests, entry points, documentation, and environments from framework-neutral platform code.

## Required Layout

Use this structure for each model project:

```text
train-model/
└── <project-name>/
    ├── README.md
    ├── conda.yaml
    ├── configs/
    │   ├── baseline.yaml
    │   └── <variant>.yaml
    ├── src/
    │   └── <python-package>/
    │       ├── __init__.py
    │       ├── data.py
    │       ├── models/
    │       ├── train.py
    │       └── evaluate.py
    ├── scripts/
    │   └── train.py
    ├── tests/
    │   └── test_*.py
    └── notebooks/                 # Optional; exploration only
```

Treat `README.md`, `configs/`, `src/`, and `tests/` as required. Require at least one YAML workload configuration under `configs/`. Keep `conda.yaml` or an equivalent project environment file at the project root. Add `scripts/` when the project needs formal training, evaluation, tuning, recovery, or promotion entry points. Add `notebooks/` only when notebooks are useful.

Allow a project to contain multiple model families and variants. Put reusable model-family implementations below `src/<python-package>/models/` and express parameter variants in `configs/*.yaml`; do not create sibling workload roots for mere hyperparameter variants.

## Enforce Ownership

- Put all model-specific Python implementation below `train-model/<project-name>/src/`.
- Put all model-specific tests below `train-model/<project-name>/tests/`.
- Reserve the repository-level `tests/` directory for platform-wide and cross-project behavior only.
- Keep model-specific fixtures and test helpers inside the same project tests directory.
- Keep shared platform code framework-neutral; do not move workload implementation into a repository-level utility module merely to make imports convenient.
- Keep datasets, checkpoints, generated models, caches, executed notebooks, and secrets out of the source tree.

Apply this bad-case rule explicitly:

```text
BAD:  tests/test_cats_dogs_tuner.py
GOOD: train-model/cats-and-dogs/tests/test_tuner.py
```

A test that names, imports, configures, or validates one workload belongs to that workload even if it currently runs from the root test suite.

## Organize YAML Configuration

- Store workload and model parameters in `configs/*.yaml`, grouped by purpose or model family.
- Keep credentials, private endpoints, and tokens out of YAML committed to Git; reference environment variables or documented external configuration instead.
- Make dataset identity, split, preprocessing, seed, resources, hyperparameters, objective metric, and optimization direction explicit when applicable.
- Avoid duplicating complete configurations for small variants when the project's loader supports clear composition or overrides.
- Keep shared service dependencies in the repository root environment only when they are truly platform-wide; keep model dependencies in the project environment file.

## Workflow

1. Read the repository instructions and inspect the target project's current files before changing paths.
2. Identify the immediate project root as `train-model/<project-name>/`; do not add another category layer between `train-model/` and the project.
3. Classify each file by ownership: configuration, implementation, entry point, test, notebook, documentation, or generated state.
4. Create or migrate the required hierarchy. Preserve behavior while updating imports, commands, documentation, and test discovery paths.
5. Ensure formal training remains parameterized and runnable outside notebooks.
6. Run the narrowest project-local tests first. Run repository-level tests only when shared or cross-project behavior changed.
7. Report any legacy files left outside the project hierarchy and explain why they could not be moved.

Do not reorganize unrelated projects as collateral work. When reviewing without authorization to edit, report violations with a proposed old-path to new-path mapping instead of moving files.

## Review Checklist

- Confirm the project is exactly one directory below `train-model/`.
- Confirm project-owned YAML exists under `configs/` and contains no secrets.
- Confirm implementation is under `src/`, not loose at the repository root or project root.
- Confirm model-specific tests are under the project's `tests/` directory.
- Confirm root `tests/` contains only repository-level or cross-project tests.
- Confirm commands, imports, README paths, and test discovery still match the hierarchy.
- Confirm runtime artifacts and notebook execution state are ignored rather than committed.
