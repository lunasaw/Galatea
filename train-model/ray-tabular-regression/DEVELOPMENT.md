# Development report

Implemented contract-first with strict RED/GREEN tests and no real training or model calls.

- Added a fixed signed Ray driver gate with Ed25519 canonical binding verification, deadline/watchdog, Ray Job
  and submission identity checks, exact role views, and one-worker enforcement.
- Added immutable S3 JSON loading, shared preprocessing/model/prediction/RMSE code, Driver-only NumPy ridge fit,
  separate evaluator model download/digest verification, and sole-owner MLflow lifecycle.
- Added exact `galatea.evidence/v1` lineage, split metric semantics, model manifest, Artifact API round-trip,
  and fresh subprocess JSON load verification.
- Added read-only plan, deterministic external release build with embedded public key/manifest, pinned project
  environment, registration/campaign examples, project contract, operator README, and forward-only tests.

Known integration requirements: the workload accepts only the signed/default `model/model.json` path so
download, upload, evidence, and Registry validation cannot diverge. The official backend must populate evaluator-only
`metadata.champion_model_sha256`, and route evaluation to the distinct evaluator cluster/account. The binding's
existing `galatea.*` metadata is checked against the official Ray Jobs record at signed `ray_address`; that
record's assigned job ID must equal the initialized RuntimeContext job ID. Placeholder registry identities cannot be registered until an administrator
replaces them with real immutable object, Release, environment, and experiment values.
