---
name: model-delivery
description: Prepare an evidence-bound Galatea candidate delivery as accepted, best-effort, or blocked after platform verification, without changing a production alias.
---

# Model Delivery

Read [references/delivery.md](references/delivery.md). Use `galatea_freeze_candidate` and `galatea_verify_candidate` from [the frozen contract](../../contracts/tools.json). Return [Agent Turn v1](../../contracts/agent-turn.schema.json); the platform report and integrity evidence control the outcome.

An accepted proposal requires platform outcome `accepted`, passed one-time final test, verified artifacts, satisfied gates, and a report reference. Final-test failure may produce platform-verified `best-effort`; failed integrity is `blocked`. State loading interface, immutable identities, metric limits, and operational constraints. Do not relax a threshold, retest the holdout, or modify a production alias.
