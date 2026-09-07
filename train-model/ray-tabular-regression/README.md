# Governed Ray tabular regression reference workload

This is the first independently governed Galatea reference workload. It fits a small NumPy ridge regressor
only inside the fixed, signed Ray Job driver and publishes a JSON model through MLflow. Local commands can
validate configuration, build a release, or run forward-only tests; they cannot fit parameters or create
training evidence.

## Security and execution contract

The MCP signs canonical `GALATEA_EXECUTION_BINDING` JSON with Ed25519 and supplies the base64 signature in
`GALATEA_EXECUTION_SIGNATURE`. `scripts/driver.py` verifies the public key embedded in the immutable release,
the deadline, then initializes Ray with `address=auto`. It queries the signed `ray_address` through the official
Ray Jobs client and requires that the signed submission resolves to the current runtime Job ID and exact signed
metadata. It also verifies role, exact data views, and `workers=1` before invoking
any fitting or MLflow code. Missing, forged, expired, direct-shell, and generic Ray invocations fail before
the fitting callback. A watchdog sends SIGTERM early enough for cleanup before the authorized deadline.

Trainer roles receive only immutable S3 `train` and `validation` JSON views. The evaluator runs on the
administrator-configured evaluator cluster/account and receives only `test`. It downloads the frozen Champion
model through the MLflow Artifact API, checks the digest bound in execution metadata, then creates a separate
evaluation Run linked by `champion_run_id` and `candidate_id`. Both roles use the same loader, standardization,
model JSON, prediction, and RMSE implementation.

The authoritative driver creates and terminates the sole MLflow Run. It logs `galatea.*` identity tags,
split-specific `val_rmse` or `test_rmse`, `model/model.json`, and `reports/evidence.json`. Success requires an
Artifact API download byte round-trip and a fresh isolated Python process loading the JSON structure. The
evidence lineage matches `galatea.evidence/v1`; final evaluation includes the Champion model SHA-256.

## Local development (no training)

```bash
python -m unittest discover -s train-model/ray-tabular-regression/tests -v
python train-model/ray-tabular-regression/scripts/check_plan.py \
  --config train-model/ray-tabular-regression/configs/baseline.yaml
python train-model/ray-tabular-regression/scripts/build_release.py \
  --output /tmp/ray-tabular-regression-v1.zip \
  --execution-public-key /secure/admin/execution-public.pem \
  --registration-output /tmp/ray-tabular-registration \
  --environment-identity 'sha256:REPLACE_WITH_REAL_LOCK_DIGEST'
```

Tests cover boundary denial, signed runtime matching, role view isolation, deterministic forward inference,
evidence shape, and deterministic packaging. They use mocked fitting/checkpoint boundaries and never perform
an optimizer step, full fit, checkpoint creation, data upload, MLflow Run, model call, or final-test read.

## Registration

Copy `registry/project.json` into the MCP administrator registry and replace every `REPLACE_*` value plus all
illustrative digests/sizes with values derived from immutable objects and the built release. Register the
same release bytes twice under trainer/evaluator IDs if desired, while backend role routing must map evaluator
to a distinct cluster and credential environment. The trainer credentials must not read the test bucket; the
evaluator credentials must not read train/validation. Keep the signing private key only in MCP configuration.

The workload accepts the signed `model_artifact_path` only when it equals `model/model.json`, which is also
the default, and rejects any other path before creating a Run. The evaluator
also requires `metadata.champion_model_sha256`; the MCP must bind it from verified Champion evidence before
submission.

## Real end-to-end run (pending administrator authorization)

1. Freeze three distinct versioned S3 objects containing JSON arrays with numeric `feature_1`, `feature_2`,
   `feature_3`, and `target`; compute content, manifest, split, and holdout digests. Do not upload from this
   development workflow.
2. Create the MLflow experiment and Artifact proxy permissions. Give the trainer write access to its Run and
   train/validation data. Give the evaluator a separate account, test-only data access, Champion Artifact read,
   and evaluation Run write.
3. Generate Ed25519 keys in the administrator environment. Build the release to an external path, publish it
   immutably, and verify its digest. Populate `registry/project.json`, validate it through the MCP registry,
   then register `campaign.example.json` after replacing the approval, IDs, budgets, and digests.
4. Through Galatea MCP only: plan and submit baseline; observe Ray and MLflow completion; verify
   `reports/evidence.json`; compare validation evidence; freeze the candidate; plan and submit a clean Champion.
5. Verify Champion Artifact round-trip evidence. Atomically claim the registered holdout. Plan evaluation with
   `champion_run_id`, `candidate_id`, and verified `champion_model_sha256`; confirm backend selection resolves
   to the evaluator cluster/account before submitting once.
6. Reconcile the evaluation Ray Job and separate MLflow Run. Confirm `test_rmse`, model digest linkage, quality
   gate, holdout marker, and final delivery outcome. Alias promotion remains a separate explicitly authorized
   operation.

Never run `scripts/driver.py` directly, invoke its workload function locally, fabricate Ray metadata, use a
generic `ray job submit`, or use the test view for tuning/debugging. Any such result is invalid evidence.
