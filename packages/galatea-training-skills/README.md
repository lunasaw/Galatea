# Galatea training skills

This is a self-contained, versioned bundle of six Codex skills for registered Galatea Campaigns. Skills provide decision methods, the Galatea MCP enforces execution and authorization, and the training Agent Runtime owns waiting and recovery. The installed bundle requires neither a Galatea checkout nor direct MLflow, Ray, database, or object-store credentials.

## Local verification and packaging

Python 3.11 or newer is sufficient; validation and packaging use only the standard library. From this directory:

```bash
python -m unittest discover -s tests -v
python scripts/validate.py
python scripts/evaluate_scenarios.py tests/scenarios/*.json
python scripts/package.py
```

`scripts/package.py` writes `dist/galatea-training-skills.tar.gz`. Every regular file is SHA-256 recorded in `MANIFEST.json`; tar metadata and gzip timestamps are fixed, so identical inputs create identical bytes. Archive entries are regular, read-only files. Validate a release without any source checkout:

```bash
mkdir /tmp/galatea-skill-check
tar -xzf dist/galatea-training-skills.tar.gz -C /tmp/galatea-skill-check
python /tmp/galatea-skill-check/galatea-training-skills/scripts/validate.py \
  /tmp/galatea-skill-check/galatea-training-skills
```

Install the extracted versioned directory read-only under the service account's configured Codex Skill discovery root. Keep old digests available for resumed tasks; activate updates only for new tasks. The Runner must compare `skill-lock.json` with its runtime lock before a Turn. It explicitly supplies the installed `skills/training-campaign/SKILL.md` through `SkillInput`, rather than accepting a model-selected path.

## Publishing the MCP contract

`contracts/tools.json` is generated, never hand-edited. In the build checkout, export it from the fixed MCP release and then refresh the two lock digests:

```bash
python scripts/export_contracts.py \
  ../../services/galatea-mcp/src/galatea_mcp/contracts.py contracts/tools.json
python scripts/validate.py
```

After export, this package validates and runs independently. Runtime code does not import the MCP package.
The cross-release synchronization test is owned by the repository's `tests/test_training_skill_contract.py`;
it is not part of the installed bundle's tests. Deployment and recovery steps are in the repository's
training Agent end-to-end runbook; this bundle remains usable without that checkout.

## Frozen scenario scope

The ten JSON fixtures contain synthetic platform responses, recorded tool traces, and structured results. `evaluate_scenarios.py` deterministically checks allowlists, forbidden actions, expected final actions, evidence, and acceptance prerequisites. Passing them proves the recorded traces satisfy the contract; it does not claim that a language model will choose those traces.

## Pending real environment acceptance

No model, training, holdout, deployment, or external service call occurs in local tests. Before production, perform a separately budgeted smoke with the pinned official `openai_codex` SDK and Codex version: use a clean service account, normal session directory, read-only installed bundle, explicit `SkillInput`, deny-all escalation mode, the real HTTPS MCP allowlist, and a fake/no-training Campaign. Record expected and observed Skill discovery, MCP authentication and tool list, structured Agent Turn validation, thread resume with the same bundle digest, process cleanup, and usage accounting. This acceptance remains pending until those observations and evidence locations are recorded.
