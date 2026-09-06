# Execution classification

Use this decision table before running model or evaluation code.

## Training Run triggers

Classify an action as a **Training Run** when any one of these is true:

- an optimizer step, parameter update, adapter update, or persistent optimizer/scheduler/RNG state occurs;
- a complete epoch or material portion of a real dataset is traversed for fitting;
- a checkpoint, adapter, trained model, candidate model, or resumable training state is produced;
- a durable MLflow Run or report is intended to serve as training, comparison, tuning, or candidate evidence;
- the action is called training, fine-tuning, retraining, tuning, smoke training, baseline training, Trial,
  or Champion execution, regardless of dataset size or promotability.

For a project declaring `spec.executionBackend: ray`, all of these actions must use the project's fixed
Galatea-authorized Ray Driver. The rule applies even to one step and even when the data is synthetic,
redacted, privately authorized, or experimental-only.

## Actions that may stay local

Local execution is limited to actions that do not become Training Runs:

| Class | Local examples | Required boundary |
| --- | --- | --- |
| Read-only inspection | source review, MLflow API queries, config comparison | no mutation |
| Contract validation | schema, YAML, secret scan, manifest/digest recomputation | no Run or model load required |
| Planning | release preview, Galatea readiness plan, resource estimate | no training Run |
| Component test | tokenizer/mask test, tensor shape test, forward-only finite-loss fixture, mocked checkpoint serialization | no optimizer step, real checkpoint, durable evidence, or test access |
| Inference-only smoke | bounded generation on approved validation fixtures | clearly labeled inference-only; no candidate or training claim |

Temporary files produced by component tests must stay outside durable Artifact locations and must not be
presented as MLflow, Ray, validation, or promotion evidence.

## Backend decision

1. Read `galatea.project.yaml`.
2. If `executionBackend: ray`, use immutable Release -> Galatea plan/authorization -> fixed Ray Job.
3. If another recoverable backend is explicitly declared, follow that project contract and preserve the
   same architecture across experiment and candidate roles.
4. If no governed backend is declared and the request would create durable experiment/candidate evidence,
   block execution and repair/onboard the project first.
5. If uncertain whether an action mutates training state or creates durable evidence, classify it as a
   Training Run.

## Non-exceptions

None of these allow local training or a generic Ray submission:

- `experimental_only`;
- `formal_training_eligible=false`;
- `promotable=false`;
- `human_review_completed=false`;
- owner approval or bulk approval;
- synthetic, public, private, or redacted data;
- a small epoch count, short expected duration, one GPU, or one worker;
- a request to collect only hashes rather than generated text.

Those fields control authorization, claims, test access, and promotion—not the execution backend.
