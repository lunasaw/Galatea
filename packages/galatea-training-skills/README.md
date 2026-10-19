# Galatea training skills

This is a self-contained, versioned bundle of six Codex skills for registered Galatea Campaigns. Skills provide decision methods and Galatea MCP enforces execution and authorization. The installed bundle requires neither a Galatea checkout nor direct MLflow, Ray, database, or object-store credentials.

The current repository runtime is native Codex, hosted by the generic `agents/codex-app-server` Web Host or used directly in repository workflows. The Host discovers configured Skills and connects to external MCP services; it contains no Galatea service or training scheduler.

This bundle still preserves its v1 `Agent Turn` contract, `wait_external` instructions, and historical Python SDK source lock. The Python Runner that consumed them has been removed. Loading these Skills into the current Host does **not** install a Campaign scheduler, automatically resume a Turn, or establish compatibility with the Host's pinned runtime. Campaign integration and waiting/recovery require separate implementation and acceptance. Repository workflows in `.codex/skills/` use their own fixed project entrypoints.

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

Install the extracted versioned directory read-only. For the current Host, set `skills.roots` in `agent.json` to the extracted bundle's `skills/` directory, which contains the six `<skill>/SKILL.md` directories. The Host registers extra roots through native `skills/extraRoots/set`; MCP connections are configured separately in `codex-home/config.toml`. Keep old digests available for resumed tasks and activate updates only for new tasks.

`skill-lock.json` records the bundle's frozen contract and historical `openai_codex`/`SkillInput` assumptions. It is not a current Host runtime lock or an accepted deployment manifest. Validate Skill discovery, output semantics and external-operation recovery before enabling a Campaign workflow.

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
training Agent documents. Their old Runner runbook is historical; use the current Host deployment and
acceptance documents for a new integration. This bundle remains independently packageable without that checkout.

## Frozen scenario scope

The ten JSON fixtures contain synthetic platform responses, recorded tool traces, and structured results. `evaluate_scenarios.py` deterministically checks allowlists, forbidden actions, expected final actions, evidence, and acceptance prerequisites. Passing them proves the recorded traces satisfy the contract; it does not claim that a language model will choose those traces.

## Pending real environment acceptance

No model, training, holdout, deployment, or external service call occurs in local tests. Validate the current Host and pinned native Codex separately using a clean service account, read-only bundle, explicit MCP configuration and a fake/no-training Campaign. Record Skill discovery and loading, MCP authentication and tool visibility, native approvals, output validation, thread recovery, process cleanup and usage accounting. Explicitly verify who observes an external operation and resumes the next decision; the generic Host does not consume `wait_external` as a scheduling instruction. Real training acceptance remains separate and requires an authorized workload and budget.
