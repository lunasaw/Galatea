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

## Select the cluster and upload identity

For a new machine, migrated paths, multiple Ray instances, Release uploads, or platform integration failures,
read [the platform runtime memory](../../../doc/platform-runtime-memory.md). Treat its machine values as
observed examples, not defaults for another host. Verify installed Ray/MLflow versions against the project lock.

Select an explicit cluster profile and distinguish GCS, HTTP Jobs/State API, and Ray Client addresses.
Verify the profile against its unit, nodes, allocated GPUs/resources, MLflow URI/Experiment, and Runtime
Bucket/Prefix. Never choose production via auto-discovery, a hardcoded development endpoint, or a bare
`ray status`. On shared hosts, stop/restart only the selected unit or Job; do not use global `ray stop`.
Head/Worker service environments need their own correct cluster addresses, not only the submitting shell.

Use current-session authorization without asking again for actions already covered. An end-to-end training
request includes the necessary immutable project Runtime Release upload within the agreed target and budget;
analysis or code edits alone do not. Distinguish data access/upload, publishing, compute, final test, and
production promotion. A full-access preset or historical approval is not an enduring grant for new tasks.

Upload with the authorized publisher identity. Keep Runtime reader credentials on Head/Workers before
`ray start`, and MLflow Artifact credentials on the server. Do not use either identity for publishing or put
long-lived credentials in Job env vars. If a file/permission is missing, resolve it within authorized scope;
otherwise report the specific blocker and prepared manifest rather than switching to an administrator account.

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
