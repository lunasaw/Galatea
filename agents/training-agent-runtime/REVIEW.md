# Runner correctness review

Reviewed against `doc/train-agent/implementation/03-codex-adapter.md`,
`doc/train-agent/implementation/05-runner.md`, the public Galatea MCP schemas, and the package tests.
No model, training job, Ray, MLflow, MinIO, or remote mutation was used.

## Fixed findings

- **High — exhausted state could strand a valid frozen candidate.** Unknown usage correctly prevents
  further inference, but it also prevented deterministic finalization. The Runner now invokes
  `galatea_verify_candidate` with the exact project, campaign, and candidate identity and accepts only a
  verified accepted/passed or best-effort report.
- **High — SDK acceptance blocked observation and cancellation.** The service previously ran local
  inference preflight before its first MCP read. Local SDK, Runtime, executable, and Skill checks now run
  at the `run_turn` boundary. MCP contract validation remains a startup requirement.
- **High — Skill and Runtime attestation were incomplete.** Registration hashed only `SKILL.md`, so a
  changed referenced file was invisible. Preflight trusted a version string without hashing executable
  bytes. The complete regular-file Skill bundle and the resolved Runtime executable are now hashed.
- **Medium — delayed evidence could churn decisions.** A succeeded operation with no candidate/report
  now enters `waiting_evidence`; polling remains read-only and preserves unknown-usage safety.
- **Medium — approval recovery prescribed an impossible action.** Registration intentionally rejects an
  existing campaign. A queued approval response can now bind a strictly newer platform-authorized
  revision and resume the exact Thread. Unsolicited, stale, and decreasing revisions remain blocked.

Cancellation ordering was reviewed and retained: both the CLI and Runner persist the MCP campaign
cancellation before signalling the worker, then request operation stops and wait for terminal facts.
Supervisor replacement remains conservative: a live or ambiguous process group blocks replacement, and
normal/timeout/cancellation exits reap the complete owned process group before clearing its marker.

## Remaining deployment acceptance

The shipped runtime lock is intentionally pending and must be populated with an accepted published SDK
artifact, exact Runtime executable digest/version, and complete Skill-bundle digest. Real HTTPS identity,
Linux systemd/cgroup containment, real Skill discovery, and an explicitly budgeted model call remain
deployment acceptance work. Governed training and promotion require separate user authorization.
