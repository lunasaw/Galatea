---
name: governed-training-workflow
description: Enforce the repository's governed data-to-model workflow when planning, implementing, running, reviewing, or auditing dataset preparation, baselines, smoke runs, training, fine-tuning, tuning, evaluation, Trial/Champion selection, or promotion. Use it whenever work may update model parameters or produce training evidence; do not use it for inference-only application work with no training lifecycle.
---

# Governed Training Workflow

Keep governance status and execution architecture separate. Labels such as `experimental_only`,
`formal_training_eligible=false`, `human_review_completed=false`, private, synthetic, smoke, or
non-promotable constrain how evidence may be used; they never authorize a different training backend.

## Classify before acting

Read [references/execution-classification.md](references/execution-classification.md) whenever the request
could execute model code, create a baseline, or evaluate a candidate. If any Training Run trigger applies,
treat the action as training. When classification is ambiguous, fail closed as a Training Run.

Inspect the project contract before proposing commands:

- repository and project `AGENTS.md` files;
- `train-model/<project>/galatea.project.yaml`;
- the selected YAML config, fixed entrypoints, README, runbook, and tests;
- immutable dataset/split identity and the declared execution backend.

For a project declaring `spec.executionBackend: ray`, every Training Run—including a smoke, private
experiment, baseline, Trial, tuning trial, retraining, or Champion—must use the same governed path:

```text
immutable Dataset Snapshot and Split
  -> explicit Authorization and role
  -> immutable Release
  -> Galatea readiness plan and evidence-bound authorization
  -> fixed Ray Job Driver
  -> Driver-owned MLflow Run
  -> train plus validation-only selection
  -> checkpoint/model/report publication
  -> MLflow Artifact API round-trip verification
  -> terminal Ray and MLflow evidence
```

Do not run the Driver from a shell, remove a check/plan flag to start training, submit a generic
`ray job submit`, or call the training function with fabricated runtime metadata. The project must enforce
these boundaries in code and tests, not only in prose.

## Preserve one architecture

Smoke, Trial, experiment, and Champion must reuse the same project-owned data loader, preprocessing,
model construction, training worker, Driver, evaluation implementation, checkpoint format, tracking code,
and Artifact verification. Configuration may change data identity, role, authorization, resource budget,
hyperparameters, test access, and promotability. It must not select an ad-hoc local training path.

Run Base, Prompt-only, and adapted-model comparisons through the same frozen split and evaluation
protocol. Do not create a separate full-dataset local baseline and combine it later with a governed Trial.
Trial selection uses train/validation only. A Champion may access test only after candidate evidence is
frozen and an atomic test-once claim is bound to the governed submission.

## Require evidence

Read [references/evidence-contract.md](references/evidence-contract.md) when designing configuration,
MLflow logging, reports, quality gates, candidate selection, or an audit. Require identity, execution,
performance, model-quality, integrity, and governance evidence. A Ray `SUCCEEDED` status proves execution
only; it does not prove model quality, safety, human approval, or production eligibility.

Use MLflow Tracking, Artifact, and Registry APIs. Never inspect `mlflow.db`, the MLflow server's MinIO
filesystem, or server-side object-store credentials. Do not persist sensitive prompts or generated text
when hashes and aggregate metrics suffice.

## Handle requested work

For an audit, report each missing state transition and whether existing evidence is governed,
diagnostic-only, or invalid. Do not upgrade historical local results by relabeling them.

For implementation, repair the project contract, fixed entrypoints, fail-closed gates, tests, and docs
before running training. A direct local or generic-Ray path capable of producing model updates or durable
evidence is a blocking defect for a Ray/Galatea project.

For execution, first verify data authorization, immutable Release, readiness, resources, split isolation,
and explicit user authority for the compute cost. Analysis or code changes do not by themselves authorize
GPU training, final-test access, model registration, or alias changes.

For promotion, require compatible validation evidence, a clean candidate freeze, test-once evidence,
Artifact round-trip, configured quality/safety gates, and explicit review. Promotion remains a separate
action from training.
