# Stage Contracts And Tools

> Current phase: SDK foundation only. Stage execution tools are planned and not registered.

## Active MCP Tools

The only active platform tools are read-only inspection tools:

- `mcp__galatea-platform__list_training_projects`
- `mcp__galatea-platform__inspect_project_structure`
- `mcp__galatea-platform__check_service_health`
- `mcp__galatea-platform__inspect_mlflow_experiment`
- `mcp__galatea-platform__inspect_ray_status`

## Planned Stage CLIs

The following CLIs exist only to report structured unsupported status:

- `agent/scripts/run_data_stage.py`
- `agent/scripts/run_training_stage.py`
- `agent/scripts/run_inference_stage.py`

They must not submit jobs, write datasets, evaluate models, or promote registry aliases until
stage-specific SDK MCP tools and approval policy are implemented.

## Next-Phase Contract Template

Every business-stage tool should specify:

- Tool name and MCP input schema.
- Side effects and idempotency key.
- Required approvals and denial behavior.
- Artifact or result schema.
- Recovery and retry semantics.
- Tests required before allowlisting.

## Validation

A new tool is not current capability until it has implementation, tests, docs, and scoped
runtime allowlist entries.
