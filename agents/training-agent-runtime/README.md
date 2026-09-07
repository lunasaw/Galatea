# Independent training-agent-runtime

Python Runner → official `openai_codex` SDK → original Codex app-server → external Galatea MCP.
This package has no Galatea, Ray, MLflow, plugin, or internal session-database imports.
One process owns one campaign and a local durable volume. Training only occurs through MCP's
registered governed workload. Runner output is a proposal; platform records decide completion.

## Install and test without models

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m unittest discover -s tests -v
```

For source development, `PYTHONPATH=src python3 -m unittest discover -s tests -v`.
The tests use SDK boundary fakes and real official MCP stdio transport against an in-process fake
service. They do not request a model or connect to Ray, MLflow, MinIO, or a remote host.
The base distribution intentionally does not claim that the inspected development Codex SDK is an
available release. Install the separately accepted official SDK/runtime pair from verified artifacts
before deployment. `deploy/runtime-lock.json` is **pending** and preflight refuses model execution.
See [DEVELOPMENT.md](DEVELOPMENT.md) for source API provenance and acceptance gaps.

## Deploy and operate

1. Create a dedicated `training-agent` service account with its home and Codex sessions on the durable
   `/var/lib/training-agent` volume. Never repurpose a developer's HOME or CODEX_HOME.
2. Install this wheel and a separately verified official Codex SDK/runtime pair into `/opt/training-agent`.
   Keep executable runtime on the allowlisted PATH. Supply only model authentication and a scoped MCP
   token in a mode-0600 `/etc/training-agent/runtime.env`; never include Ray/S3/admin credentials.
3. Install the independent skill bundle read-only. Set the actual training-campaign `SKILL.md` path.
   Provision the registered project/campaign/authorization through the platform trusted admin flow.
4. Copy the config and runtime lock templates to `/etc/training-agent`. Set real paths and HTTPS MCP
   endpoint. Record immutable SDK/runtime artifacts, accepted version output and Skill/config digests;
   change `acceptance_status` only after the real acceptance procedure below succeeds. Pending lock is
   an explicit deployment gate, not a placeholder silently bypassed by the Runner.
5. Register and perform read-only preflight, then install the example unit after adapting paths:

```bash
training-agent --config /etc/training-agent/config.json register --revision 1 --prompt 'Run the authorized campaign'
training-agent --config /etc/training-agent/config.json preflight
systemd-analyze verify deploy/training-agent.service
training-agent --config /etc/training-agent/config.json run --once
```

`run --once` is one bounded tick. Service startup validates the MCP contract, while SDK, Runtime,
executable digest, and complete Skill-bundle acceptance are checked immediately before a model turn.
This permits read-only observation and cancellation while inference dependencies are unavailable.
It may call a real model after accepted inference preflight. Omit `--once` for
the service loop. Explicit model and training budgets are required before running it in production.
Waiting ticks query MCP only. Default total model decisions are 20, per-turn deadline 300 seconds,
and token observation threshold 100000; these stop new decisions rather than impose a billing cap.

```bash
training-agent --config /etc/training-agent/config.json respond --revision 1 --response 'Requested clarification'
training-agent --config /etc/training-agent/config.json cancel
```

Responses enter a durable inbox while the Runner owns its lifetime lock. Approval prose never grants
resources: the platform authorization must actually change before approval recovery can proceed.
Cancellation first writes the platform cancellation marker, then queues worker interruption; polling
supervisor signals the worker and terminates its entire process group. Runner sends stop requests and
keeps `cancelling` until platform operations are terminal. Stop the systemd service to stop the Runner
and all app-server descendants; that alone does **not** cancel a training job.

## Recovery

Keep `campaigns/<id>/state.json`, `worker/identity.json`, worker group marker, original workspace,
read-only runtime/Skill configuration and the dedicated Codex session volume together. Atomic writes
use same-directory temporary files, fsync, replace, and directory fsync. No NFS or cross-host takeover.

On restart the Runner checks the old process group before inference, recovers Thread/Turn identity
from the durable worker journal, and queries every paginated operation. Live/unknown old process groups
block takeover; an operator must confirm/clean the original execution group, not delete its marker
blindly. Corrupt state is preserved and raises an error. Lost turn usage pauses new inference but still
observes existing operations. A verified platform report can finish delivery without spending another
model turn. Submitting/unknown operations always wait; no model is asked to reconstruct a missing ID.

A pending request's revision cannot be changed by `respond`, and registration remains non-overwriting.
When an approval response is queued and the platform exposes a strictly newer authorized revision, the
Runner binds that revision to the existing request and resumes the exact Thread. Other revision changes
remain blocked. Runtime or complete Skill-bundle digest changes refuse automatic resume.

## Real-environment end-to-end acceptance

Record expected, observed, evidence reference, and status for each step. These checks are **pending**;
local fake success must not be described as production acceptance.

1. In an isolated account, inspect `python -m pip show openai-codex`, exact `codex --version`, Python
   version and artifact hashes. Confirm the accepted SDK exports `Codex`, `CodexConfig`, `SkillInput`,
   `TextInput`, `ApprovalMode.deny_all`, `Sandbox.workspace_write` and thread start/resume/turn handle
   APIs. Match CLI PATH to the runtime lock; do not adapt another protocol to mimic the SDK.
2. Configure a fake-only MCP account with all 17 tools. Verify HTTPS token authentication, tools/list,
   required service failure, tool allowlist, scoped refusal, and no administrator/back-end credentials
   visible inside the worker or app-server. Verify configured `required` semantics with Python
   app-server rather than assuming exec behavior.
3. With an explicitly approved small model budget, run one decision, verify `thread_id` is durable
   before turn initiation and `turn_id` before stream collection. Confirm Skill discovery from the
   independent install and complete output-schema response. No real training is necessary for this step.
4. Simulate a queued/running operation on the fake backend; execute at least two `run --once` ticks.
   Verify decision_seq and SDK invocation count remain unchanged. Make it terminal, resume the exact
   thread from the same persistent sessions, and validate delivery against the platform's report.
5. Kill the worker after simulated submission but before final output, restart the Runner, and confirm
   the original operation is observed with no second submission or model call. Repeat at Thread save,
   Turn save, output save and post-turn platform-read boundaries. Missing usage must become unknown.
6. Run a worker that ignores interruption and creates descendants. Verify timeout and cancellation clean
   its complete execution group. Kill the parent Runner mid-turn: restarting must refuse a still-live
   old group. Confirm systemd KillMode handles escaped SDK lifetime inside its control group.
7. Exercise pending input, stale revision, approval with unchanged authorization, platform unavailable,
   corrupted state, invalid/foreign output references, missing report, failed integrity and final-test
   gate. All must fail closed while retaining operation references.
8. Only after separate explicit training authorization, connect a registered governed Release and a tiny
   workload budget. Validate Ray identity, MLflow Artifact accessibility, frozen data/cohort, final-test
   once, accepted/best-effort evidence, and cancellation. Keep sensitive examples out of evidence logs.

Production release requires recorded evidence for SDK/runtime pairing, authenticated transport,
Linux process containment, real Skill discovery and the governed workload; this implementation does
not claim those were exercised during local development.
