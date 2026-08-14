# Galatea Agent Current Architecture

> Status: SDK foundation only. This phase keeps Claude Agent SDK runtime,
> in-process MCP tools, hooks, permissions, schemas, state helpers, commands,
> and CLI entry points. Business agents and stage execution tools are planned
> for the next phase and must be added explicitly through SDK MCP tools.

## Runtime Boundary

```text
User / CLI / Notebook
  -> GalateaRuntime / GalateaSDKRuntime
  -> ClaudeSDKClient + ClaudeAgentOptions
  -> in-process galatea-platform MCP server
  -> read-only platform inspection tools
```

The runtime must stay SDK-native:

- Create model sessions through `ClaudeSDKClient`.
- Configure behavior through `ClaudeAgentOptions`.
- Register platform tools with `create_sdk_mcp_server` and `@tool`.
- Use SDK `AgentDefinition` for subagents.
- Use SDK `SessionStore` only for transcript mirror/resume stores.
- Use hooks and permission policy as safety boundaries.

## Current Components

| Area | Files | Current role |
| --- | --- | --- |
| SDK runtime | `agent/core/sdk.py`, `agent/runtime.py` | Build SDK options, collect messages, validate structured output, budgets, hooks, MCP and sessions. |
| MCP tools | `agent/tools/server.py`, `agent/tools/inspection.py` | Five read-only inspection tools for projects, services, MLflow experiment metadata, and Ray status. |
| Commands | `agent/commands/` | Claude Code-style prompt command planning with scoped tools. |
| Agents | `agent/agents/definitions.py`, `agent/agents/registry.py` | SDK `AgentDefinition` presets and SDK-native registry. |
| Hooks | `agent/hooks/` | SDK-supported hook events and adapters to SDK `HookMatcher`. |
| Policies | `agent/policies/` | Budget, permission, and quality gate helpers. |
| Schemas | `agent/schemas/common.py`, `agent/schemas/inspection.py` | Shared stage and inspection result schemas. |
| State | `agent/state/store.py`, `agent/state/experiment.py` | Galatea application state; not SDK transcript storage. |
| Workflows | `agent/workflows/state_machine.py`, `agent/workflows/orchestrator.py` | Generic workflow state machine and handler orchestration. |
| Scripts | `agent/scripts/` | Platform inspection CLI plus explicit unsupported status for planned stage CLIs. |

## Current Tool Surface

Only these platform MCP tools are currently implemented and registered:

- `mcp__galatea-platform__list_training_projects`
- `mcp__galatea-platform__inspect_project_structure`
- `mcp__galatea-platform__check_service_health`
- `mcp__galatea-platform__inspect_mlflow_experiment`
- `mcp__galatea-platform__inspect_ray_status`

Any data, training, inference, promotion, documentation update, or write action is planned only.
The corresponding CLI entry points return structured `unsupported` results until registered SDK
MCP tools and approval policy are implemented.

## Session Boundary

`AgentSDKConfig.session_store` is reserved for Claude SDK transcript stores implementing
`append(key, entries)` and `load(key)`. `agent.state.AgentStateStore` stores Galatea application
metadata only and must not be passed as an SDK transcript store.

## Business Agent Integration Rules For Next Phase

- Add business tools as named MCP tools under `agent/tools/`.
- Add schema contracts before exposing a tool to the model.
- Add focused tests before wiring tools into `allowed_tools`.
- Keep destructive or expensive operations behind explicit approval.
- Do not use direct Bash as the implementation boundary for platform actions.
- Keep production model alias changes and long training jobs out of default `dontAsk` flows.

## Validation

Run the active agent tests with:

```bash
python -m unittest discover -s agent/test -p 'test_*.py'
```
