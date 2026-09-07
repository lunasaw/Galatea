# Campaign routing and recovery

Use this sequence for every decision:

1. Fetch capabilities and the current Campaign; reject a protocol or request-revision mismatch.
2. List operations before planning. Reattach to pending, unknown, queued, or running work. Observe an unknown submission before any retry.
3. Route only through the current authorized slot. `galatea_plan_run` is the authoritative budget/readiness check. Submit its returned plan once with a stable idempotency key.
4. End with `wait_external` immediately after a successful submission. On a later Turn, use fresh operation and evidence state.
5. Select candidates from compatible validation evidence. Freeze the chosen run once. Final test is a one-time isolated platform operation.
6. Complete only from `galatea_verify_candidate` evidence. An `accepted` proposal requires a passed final test, platform report reference, and integrity evidence.

Cancellation, exhausted budget, missing capabilities, invalid Release/configuration/data, or an unreconciled operation stops new work. Final-test failure yields `best-effort` when verified deliverable evidence remains valid, otherwise `blocked`. Never change gates or revisit the same holdout to improve the result. Alias promotion is outside this bundle.
