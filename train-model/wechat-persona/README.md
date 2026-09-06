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
python train-model/wechat-persona/scripts/import_chat.py --config train-model/wechat-persona/configs/import.yaml --check
python train-model/wechat-persona/scripts/build_dataset.py --config train-model/wechat-persona/configs/import.yaml --plan
python train-model/wechat-persona/scripts/review_app.py
python -m unittest discover -s train-model/wechat-persona/tests -p 'test_*.py'
```

Writing data is always explicit and versioned. `import_chat.py --execute` requires a verified
consent ledger, an allowed raw root, an explicit source and speaker mapping; it writes only
redacted messages, sessions, uncertain review candidates, lineage, and aggregate reports.
`build_dataset.py --execute --check-approved` refuses empty splits, missing reviewer evidence,
PII scan failures, unknown roles, cross-split sessions/duplicate groups, and existing output
directories. Review events and deletion receipts contain object IDs and hashes, never chat text.

`submit_train.py --run`, `evaluate.py --run`, and all model-producing actions require the
Galatea-authorized Ray boundary.  Until consent and human review are verified, configs remain
diagnostic/blocked and synthetic fixtures cannot be represented as real-data evidence.

The current repository delivery is code-and-contract complete but formal-evidence blocked: it
does not perform real optimizer steps, create adapters/checkpoints, read final-test data, or change
a Model Registry alias.  Once an authorized immutable dataset and Release exist, Galatea supplies
the fixed Driver's runtime binding; direct shell and generic Ray submissions remain invalid.
