# wechat-persona

`wechat-persona` is the governed, local-only workload for the project 5–9 design.  It keeps
data engineering, removable relationship memory, style adaptation, evaluation, and prototypes
as separate contracts.  The package is intentionally dependency-light so contract checks can run
without model weights, Ray, or MLflow.

All training and durable evaluation paths are fail-closed: a check/plan command is read-only and
the fixed Ray Driver accepts a run only with an immutable release, Galatea readiness digest,
execution identity, and explicit role binding.  Raw exports, identities, checkpoints, adapters,
indexes, generated text, and deletion receipts belong under `platform-data/llm-private/wechat-persona/`
and must never be committed.

## Read-only checks

```bash
export PYTHONPATH="$PWD/train-model/wechat-persona/src"
python train-model/wechat-persona/scripts/import_chat.py \
  --config train-model/wechat-persona/configs/import.yaml \
  --check --source <controlled-export> --consent <controlled-consent.json> \
  --speaker-map-file <controlled-speaker-map.json>
python train-model/wechat-persona/scripts/build_dataset.py --config train-model/wechat-persona/configs/import.yaml --plan
python train-model/wechat-persona/scripts/review_app.py
python -m unittest discover -s train-model/wechat-persona/tests -p 'test_*.py'
```

The import check runs the complete consent, normalization, split, and layered
privacy preflight in memory. It fails closed when any controlled input or
speaker binding is missing and does not create an output directory.
Session and review-candidate v2 records preserve redacted source-message
boundaries in versioned fields so privacy checks never infer them from merged
prompt text. The source manifest and privacy report also record aggregate
cross-message `<SECRET>` substitution counts without retaining matched values.

## Human review export

The importer writes `review/candidates.jsonl` with every candidate initially marked
`uncertain`. Review decisions are append-only: keep the ID/hash-only event log separate
from the controlled content record used only for `redact_keep` edits.

```bash
python train-model/wechat-persona/scripts/review_app.py \
  --candidate <candidate.json> \
  --event-log <controlled-events.jsonl> \
  --reviewed-row-log <controlled-reviewed-rows.jsonl> \
  --status redact_keep --reviewer-id <reviewer-id> \
  --reason '<why it was edited>' --edited-candidate <edited.json>

python train-model/wechat-persona/scripts/compile_reviewed.py \
  --candidates <dataset>/review/candidates.jsonl \
  --candidate-manifest-sha256 <dataset-candidate-manifest-sha256> \
  --events <controlled-events.jsonl> \
  --reviewed-rows <controlled-reviewed-rows.jsonl> \
  [--selection-manifest <controlled-selection-manifest.json>] \
  --output <new-controlled-reviewed.jsonl> \
  --review-summary <new-controlled-review-summary.json>
```

The compiler requires exactly one review event per candidate, rejects missing or duplicate
IDs, excludes `reject` and `uncertain`, requires reasons and edited content for
`redact_keep`, runs a second PII scan, and writes a non-overwriting hash/count-only review
summary for the formal snapshot gate. It never edits the candidate file. If a smaller
audited subset is desired, record the selection rule and selected IDs separately; an
unselected candidate is not treated as reviewed.

The hash/count-only summary binds the candidate manifest, reviewed and event file
digests, decision counts, and per-split rejects. Store it in the controlled review
directory; it contains no chat text. Import and review writers force controlled
directories to mode `700` and generated JSON/JSONL evidence to mode `600`.

Writing data is always explicit and versioned. `import_chat.py --execute` requires a verified
consent ledger, an allowed raw root, an explicit source and speaker mapping; it writes only
redacted messages, sessions, uncertain review candidates, lineage, and aggregate reports.
`build_dataset.py --execute --check-approved` refuses empty splits, missing reviewer evidence,
PII scan failures, unknown roles, cross-split sessions/duplicate groups, digest or lineage
mismatches, incomplete review summaries, and existing output directories. It also requires
the source/split manifests, layered privacy report, lineage, and review summary as explicit
inputs. A generated snapshot remains non-eligible until a separate `FORMAL_DATASET_READY`
approval is recorded. For example:

```bash
python train-model/wechat-persona/scripts/build_dataset.py \
  --config <formal-config.yaml> --execute --check-approved \
  --reviewed-jsonl <controlled-reviewed.jsonl> \
  --source-manifest <dataset>/manifests/source_manifest.json \
  --split-manifest <dataset>/manifests/split_manifest.json \
  --privacy-report <dataset>/reports/privacy_report.json \
  --lineage <dataset>/manifests/lineage.jsonl \
  --review-summary <controlled-review-summary.json> \
  [--selection-manifest <controlled-selection-manifest.json>] \
  --output <new-formal-snapshot-directory>
```

Subset review is accepted only when the compiler receives the selection manifest and
the manifest file digest, parent candidate manifest, selected ID list/digest, selection
version, and pre-review split counts all match the formal config and review summary.
Rejected selected IDs remain represented by decision counts even though they are not
written to reviewed JSONL. Snapshot files are staged beside the final path and published
with a Linux atomic no-replace rename, so neither a failed write nor a racing empty
destination can replace or expose a partial snapshot directory.

Review events and deletion receipts contain object IDs and hashes, never chat text.

`submit_train.py --run`, `evaluate.py --run`, and all model-producing actions require the
Galatea-authorized Ray boundary.  Until consent and human review are verified, configs remain
diagnostic/blocked and synthetic fixtures cannot be represented as real-data evidence.

## Code-only Release builder

Build an immutable, deterministic code package before asking a platform administrator to register
the project.  The builder archives source, configuration, the fixed entrypoints, and the declared
environment; it excludes tests, notebooks, caches, `platform-data/`, and symlinks.  It does not
upload to MinIO, contact Galatea, submit Ray Jobs, create MLflow Runs, or produce model artifacts.

```bash
export PYTHONPATH="$PWD/train-model/wechat-persona/src"
python train-model/wechat-persona/scripts/build_release.py \
  --output-dir /srv/galatea-private/wechat-persona/releases \
  --registration-output /srv/galatea-private/wechat-persona/registration-v2 \
  --snapshot-manifest \
    /srv/galatea-private/wechat-persona/formal-snapshot/wechat_35ad187b65c0ff1cb4e7-formal-sft-v2/manifest.json
```

The default command requires a clean Git commit and prints the content-addressed Release ZIP,
`release_id`, manifest path, and explicit `uploaded=false`, `registered=false`,
`training_started=false`, and `mlflow_run_created=false` state.  The registration directory is an
MCP-schema-valid `projects.json` plus `campaign.json`; every `ADMIN_*`/`PENDING_*` placeholder must be replaced after
binding immutable object-store versions, Ray trainer/evaluator endpoints, MLflow permissions,
quality gates, and an approved Campaign budget. V1 does not require an Ed25519 signing key:
the MCP issues an unsigned canonical execution binding and the fixed Driver verifies the
immutable inputs, Ray submission identity, exact metadata, and runtime Job ID. Until those bindings and approvals
exist, do not run `submit_train.py --run`, submit a generic Ray Job, create an MLflow Run, or mark
the snapshot as training-eligible.

The formal role configurations are `formal-sft-v2-baseline`, `formal-sft-v2-trial`,
the role-matched Champion copies `formal-sft-v2-champion`/`formal-sft-v2-champion-trial`,
and `formal-sft-v2-evaluate`. The Release embeds normalized JSON copies
of each configuration because the MCP validates exact config bytes inside the ZIP. Before building,
run `scripts/scan_canary.py` over the frozen snapshot and bind the resulting report digest and zero
match count; never change `canary_scan_passed` without that evidence.

The current repository delivery is code-and-contract complete but formal-evidence blocked: it
does not perform real optimizer steps, create adapters/checkpoints, read final-test data, or change
a Model Registry alias.  Once an authorized immutable dataset and Release exist, Galatea supplies
the fixed Driver's runtime binding; direct shell and generic Ray submissions remain invalid.
