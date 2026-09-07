# Strategy selection

The baseline and candidate must share the registered loader, preprocessing, evaluation protocol, artifact path, and execution backend. Compare memory, accelerator availability, worker count, time allowance, checkpoint recovery, determinism, and metric semantics. Favor the least complex method capable of testing the stated hypothesis within the slot.

A recommendation is not authorization. Pass the chosen registered Release and configuration to experiment design; `galatea_plan_run` makes the authoritative feasibility decision.
