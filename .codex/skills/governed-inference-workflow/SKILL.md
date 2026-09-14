---
name: governed-inference-workflow
description: Plan, submit, observe, or update an inference-only Ray Serve deployment for a Galatea project through an immutable Release and evidence-bound Codex authorization. Use for governed model serving that does not update parameters; do not use for training, model-selection evaluation, promotion, or Registry alias changes.
---

# Governed Inference Workflow

Use `scripts/governed_inference.py` as the controller for inference-only Ray Jobs. It is independent of
DeepSeek Harness and `plugins/dsh-galatea`. Do not invoke those components from this workflow.

## Boundaries

- Keep the service loopback/private unless the project contract explicitly authorizes another bind.
- Never print or persist request prompts, retrieved memory text, generated text, credentials, or raw Job logs.
- Do not call `ray job submit` or invoke the serving Driver directly. The helper is the fixed submit path.
- Require a project-owned fixed `inferenceCheck` and `inference` entrypoint, an immutable Release, and a
  successful read-only preflight.
- Treat any optimizer step, parameter update, training metric, checkpoint creation, model selection, or
  promotion as training lifecycle work and route it to `governed-training-workflow` instead.
- Do not stop the currently healthy service until its replacement has passed private acceptance checks.

## Plan

Run `plan` before every submission. Store the plan outside the source project, normally under ignored
`platform-data/`. The helper verifies local Release files, archive safety, current-config parity with the
Release, fixed entrypoints, and the project inference preflight. It then writes a mode-0600 plan whose
readiness and execution identities are content-addressed.

```bash
/data/conda/envs/ray-llm-py312/bin/python \
  .codex/skills/governed-inference-workflow/scripts/governed_inference.py plan \
  --project-root train-model/<project> \
  --config configs/<inference-config>.yaml \
  --release-manifest /absolute/path/to/<release-id>/release.json \
  --attempt <attempt> \
  --plan-out /absolute/path/to/platform-data/governed-inference/<plan>.json
```

Review the returned project, Release ID, config digest, model manifest digest, requested resources,
readiness evidence digest, and deterministic submission ID. If any input changes, generate a new plan.

## Authorize And Submit

Immediately before submission, require explicit user authority for the exact planned Release and action.
Pass the plan's full `readiness_evidence_digest` as the authorization value; a generic boolean is not
accepted. The helper reruns all checks and rejects stale or altered plans before calling the Ray Jobs HTTP
API with fixed Galatea metadata.

```bash
/data/conda/envs/ray-llm-py312/bin/python \
  .codex/skills/governed-inference-workflow/scripts/governed_inference.py submit \
  --plan /absolute/path/to/platform-data/governed-inference/<plan>.json \
  --authorized-evidence-digest 'sha256:<readiness-digest>'
```

Submission is idempotent. An existing deterministic submission ID is reused only when all governed
metadata matches.

## Observe And Accept

Use `observe --plan <path>` for sanitized state; do not request raw Job logs when private prompts may have
been served. Inspect Ray Serve's structured applications endpoint for deployment health and topology.
Acceptance should cover the ordinary chat path, a grounded-memory question, an unsupported question, and
prompt injection. Report only pass/fail, HTTP status, response shape, and hashes or aggregate properties;
do not expose private evidence or generated text.
