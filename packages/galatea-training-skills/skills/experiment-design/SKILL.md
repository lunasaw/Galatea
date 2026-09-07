---
name: experiment-design
description: Select the next authorized Galatea experiment slot, configuration, seed, and stopping rule using compatible evidence and the Campaign's finite budget.
---

# Experiment Design

Read [references/design.md](references/design.md). Get the current Campaign, existing operations, and compatible run summaries via tools from [the frozen contract](../../contracts/tools.json). Never derive a new trial allowance from a user's broad access statement.

Choose one unused or recoverable authorized slot and a registered configuration. Preserve the configuration's seed. Define the validation objective and direction, hypothesis, resource bound, comparison cohort, and stop condition before `galatea_plan_run`. A failed plan is evidence to stop or request the missing input, not permission to run locally.
