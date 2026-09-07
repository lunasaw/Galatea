# Development and acceptance record

Implemented 2026-09-06. All implementation is local to this independently packaged directory.
No model, Ray, MLflow, MinIO, production deployment, training, or remote mutation was performed.

## Test-first implementation record

Each production behavior below began with a missing-behavior assertion, which was run and observed
failing before implementation. Full output is retained in the Codex task tool history. Tests asserting
module availability were initial executable acceptance placeholders, followed by concrete behavior
assertions; later regressions failed on incorrect behavior rather than import errors.

| Batch | Observed RED | GREEN evidence |
| --- | --- | --- |
| Durable state and cumulative usage | 2 missing implementation assertions | Atomic roundtrip, second lock rejection, corrupt state preservation, path escape rejection; 100→160 gives 60, repeated snapshot 0, regression/foreign Thread unknown |
| Official SDK adapter and output | 2 missing implementation assertions | Exact resume, deny-all/workspace-write/SkillInput/schema, thread saved before turn, turn saved before run; foreign campaign and unverified report rejected |
| Bounded Runner | 2 missing implementation assertions | Submit-then-crash finds original op; two waits do not invoke model; terminal state resumes exact thread and report completes with 160 total tokens |
| Process supervisor | 2 missing implementation assertions | Sanitized subprocess env, deadline, group cleanup, live orphan blocks replacement |
| MCP client | 2 missing implementation assertions | Pagination and strict envelope; real MCP SDK stdio initialize/tool call against fake-only server |
| Worker and trusted inputs | 2 missing implementation assertions | Non-overwriting registration, stale response rejection, durable Thread/Turn journal before effects |
| Service inbox and preflight | 2 missing implementation assertions | Responses/cancel while lifecycle lock held; pending runtime acceptance blocks inference |
| Crash recovery | Missing journal replay + observed unwanted extra model call | Durable identity replay and unknown usage block replacement inference; cancellation waits for terminal operation |
| In-turn cancellation | Missing cancellation poll assertion | Cancellation signals worker before its deadline, process group is clean |
| Report without remaining inference | Observed `blocked` instead of `completed` | Verified platform delivery finishes with unknown usage and no runtime call |
| Exhausted candidate finalization | Candidate existed but unknown usage blocked | Calls exact scoped verify_candidate and completes from its verified report without inference |
| Evidence publication delay | Terminal execution repeatedly became decision-relevant | Persists waiting_evidence and performs read-only polling until candidate/report exists |
| Inference gate placement | Pending local SDK/Skill blocked all service work | MCP observation/cancellation starts independently; local acceptance runs immediately before inference |
| Immutable local inputs | Entry file/version string could hide changed dependencies | Complete Skill-bundle and exact runtime executable bytes are SHA-256 attested |
| Approval revision recovery | Message required impossible duplicate registration | A queued approval response may bind a strictly newer platform-authorized revision |
| Explicit accepted runtime | Missing codex_bin adapter parameter | Adapter passes exact accepted runtime executable to official CodexConfig |

The official-source characterization test runs the actual `Codex`, `Thread`, `TurnHandle.run` and SDK
collector with a fake client boundary and typed notifications. It covers start, exact resume,
completed/failed/interrupted, empty output, bad JSON and missing completion. This test was added as
characterization after adapter implementation; it is not claimed as a new RED/GREEN feature cycle.
It is opt-in so this package's normal tests do not depend on any neighboring source checkout:

```bash
CODEX_SDK_SOURCE=/path/to/official-codex/sdk/python/src PYTHONPATH=src \
  python3 -m unittest discover -s tests -p test_official_sdk.py -v
```

Source inspected: official Codex checkout `459a79e`, `sdk/python/src/openai_codex/{api.py,client.py,
_run.py,_initialize_metadata.py,generated/v2_all.py}`. Source pyproject declares SDK `0.0.0-dev`,
which normalizes to `0.0.0.dev0`, and dependency `openai-codex-cli-bin==0.147.0`.
No published release identity was invented. Deployment preflight rejects the shipped pending lock.
Tested local dependencies: Python 3.11, MCP 1.26.0, JSON Schema 4.25.1, Pydantic 2.12.5.

## Ownership and public interface

- MCP calls are official `ClientSession.call_tool`; stdio test transport and HTTPS deployment transport.
  Exactly the frozen 17 names are recognized. Core tools/list preflight is mandatory in the CLI.
- Reads scope by project_id/campaign_id and paginate all operations. Cancel/stop carry a bounded reason.
- Envelope is `galatea.tools/v1`, strict boolean ok, request_id, data/error. Report references are opaque:
  no URL or filesystem assumptions. Completion requires the campaign-owned verified report.
- Model proposals validate the published agent-turn JSON Schema, exact campaign/revision/decision,
  action combinations and operation/evidence/report ownership. Accepted requires passed final test.
- Shared libraries, plugins, Galatea services and Codex session internals are never imported/read.
- Supervisor starts the worker with explicit environment allowlist and new process group. SDK does not
  receive an env overlay advertised as isolation. Worker signal uses official `handle.interrupt()`.
- The worker journal is durable before effects; Runner replays it after parent crash. Any interrupted
  active decision loses usage certainty, so it observes platform operations and prevents new inference.
- Runtime lock and complete Skill-bundle digest are bound at registration. The Runtime executable bytes,
  SDK version and Skill bundle are verified immediately before each inference turn.

## Remaining real acceptance and conservative limitations

See README's eight-step E2E procedure. Published SDK/runtime pairing, real model/Skill discovery,
HTTPS authentication deployment, Linux systemd/process-tree containment and actual governed training
remain pending. Local source SDK characterization is not release acceptance.

One campaign per Runner volume, single host, serial jobs, no NFS lease takeover. Process-group markers
cannot distinguish a recycled OS process-group ID, so ambiguous/live markers block rather than kill an
unverified process. Operator reconciliation is deliberate. Lifecycle lock is held by `run`; public
register/respond/cancel helpers are intended for the CLI, whose inbox keeps state single-writer.

State corruption or revision changes require trusted reconstruction; there is no automatic state reset,
Thread replacement, authorization minting or resource-budget increase. Unknown usage has no automatic
model restart. Token threshold only blocks future decisions, not real-time billed cost. Runtime lock
artifact digests are deployment provenance fields. Preflight checks installed SDK version, exact Runtime
executable digest/version output and the complete Skill-bundle digest. SDK artifact provenance and complete
transitive dependency verification remain deployment acceptance steps.

The repository integration suite now connects this Runner to the independent MCP service over actual
HTTP transport, with fake model/compute I/O. It covers four jobs, delayed evidence across Runner restart,
exact Thread resume and rejection of a substituted report reference. Independent package transport tests
remain self-contained; these observations do not establish live SDK/platform acceptance.
