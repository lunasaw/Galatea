# Governed evidence contract

Every governed Training Run must make the following evidence retrievable by immutable platform IDs and
official APIs. Project-specific metrics may extend this list but may not erase the distinctions below.

## State machine

```text
Dataset Snapshot
  -> Authorization
  -> Deterministic Split
  -> Project Contract
  -> Immutable Release
  -> Galatea Readiness
  -> Authorized Ray Job
  -> Driver-owned MLflow Run
  -> Train and Validation
  -> Artifact API Verification
  -> Trial Evidence
  -> Candidate Freeze
  -> Champion and Test-once
  -> Human/Safety Gates
  -> Explicit Promotion
```

A stage cannot infer the next stage. In particular, authorization does not imply review, Ray success does
not imply quality, validation quality does not grant test access, and test success does not update an alias.

## Identity and lineage

Record at minimum:

- task, project, run role, promotability, approval basis, and governance status;
- dataset ID, source/manifest SHA-256, split SHA-256 and counts, preprocessing/schema version;
- model/tokenizer ID and immutable revision;
- canonical config digest, complete hyperparameters, random seed, code revision, environment digest;
- requested CPU/GPU/memory/placement and worker topology;
- Release ID, Galatea readiness digest and execution identity;
- Ray Submission ID, Ray Job ID, MLflow Run ID, attempt ID, and retry/resume linkage.

## Training and validation quality

Keep metric names split-specific. At minimum record:

- per-step or per-epoch train loss, learning rate, gradient norm, and optimizer step;
- validation loss and perplexity (or task-equivalent objective), best checkpoint, and objective direction;
- Base/Prompt-only/adapted-model metrics under the same frozen input, prompt, generation, seed, and length
  contract when those variants are claimed;
- generalization gaps and Base-to-candidate deltas rather than only final absolute values.

For open-ended generation, include defined and versioned metrics such as token F1, ROUGE-L F1, exact match,
non-empty/format success, repetition rate, max-length stop rate, and generation success rate. An auxiliary
quality score must publish its exact formula and component values. It is not a substitute for blind human
preference, privacy, safety, factuality, or production gates.

## Performance and resources

Record enough data to diagnose both training and inference performance:

- data preparation, model load, tokenization, optimizer compute, validation, checkpoint, upload, and total
  wall time;
- samples/s, target tokens/s, optimizer steps/s, validation tokens/s;
- generation samples/s and tokens/s, p50/p95 batch latency, and mean/p50/p95 generated length;
- peak GPU allocated/reserved memory, process RSS, GPU model/count, and failure/OOM reason;
- checkpoint count/size and Artifact upload/download/verification time when material.

Report measurement population, warm-up treatment, batch size, sequence limits, and hardware so rates remain
comparable.

## Artifact and lifecycle integrity

Persist config, dataset/split manifests, reports, checkpoint metadata, adapter/model artifacts, environment
and recovery metadata through the configured Artifact service. Store a SHA-256 for every material artifact.
Verify download and fresh-process load through the MLflow Artifact API before marking the Run successful.

The authoritative Driver alone creates/finalizes the parent MLflow Run and publishes shared artifacts.
Retries create new attempts/Runs, retain old evidence, and use `retry_of` or `resumed_from`; they never
overwrite a successful path or expose an incomplete checkpoint as current.

## Test and promotion

Smoke and Trial Runs must record test access as untouched. Candidate selection uses training and validation
only. A Champion must bind the frozen candidate, protocol, split digest, and an atomic test-once claim before
loading test data. Any candidate, prompt, threshold, split, model, or protocol change invalidates the old
final-test evidence.

Registry registration or alias changes require a separate explicit action after Artifact integrity,
configured quality gates, privacy/safety checks, and human review pass.
