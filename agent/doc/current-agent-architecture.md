# Galatea Agent Current Architecture

> Status: SDK foundation only. This phase keeps Claude Agent SDK runtime,
> in-process MCP tools, hooks, permissions, schemas, state helpers, and CLI
> entry points. Business agents, prompt commands, and stage execution tools are planned
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
- Use SDK-native hook callbacks and permission flows as safety boundaries.
- Enable Skills only through `ClaudeAgentOptions.skills` and `plugins`; local
  discovery is display/preflight evidence, not authorization.

## Current Components

| Area | Files | Current role |
| --- | --- | --- |
| SDK runtime | `agent/core/sdk.py`, `agent/runtime.py` | Build SDK options, collect messages, validate structured output, budgets, hooks, MCP and sessions. |
| MCP tools | `agent/tools/server.py`, `agent/tools/inspection.py` | Five read-only inspection tools for projects, services, MLflow experiment metadata, and Ray status. |
| Agents | `agent/agents/definitions.py`, `agent/agents/registry.py` | SDK `AgentDefinition` presets and SDK-native registry. |
| Hooks | `agent/hooks/` | SDK `HookInput`, `HookJSONOutput`, callbacks, and `HookMatcher` with no schema translation. |
| Policies | `agent/policies/` | Budget, Galatea-specific allow/deny rules, and quality gate helpers. SDK owns permission modes and approval handling. |
| Skills | `agent/skills/registry.py`, `.claude/skills/` | Optional display/preflight helper plus Claude Code-native project discovery paths. Runtime authorization comes only from SDK options. |
| Schemas | `agent/schemas/common.py`, `agent/schemas/inspection.py` | Shared stage and inspection result schemas. |
| State | `agent/state/store.py`, `agent/state/experiment.py` | Galatea application state; not SDK transcript storage. |
| Workflows | `agent/workflows/state_machine.py`, `agent/workflows/orchestrator.py` | Deterministic state/evidence tracking only; it never dispatches agents or stage handlers. |
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

Prompt commands such as commit-and-push automation are also out of scope for the foundation
phase. They should return in a later feature layer only if they map cleanly to SDK permissions,
hooks, and explicit user approval.

## Permission Boundary

- `disallowed_tools` removes prohibited tools from the SDK/Claude Code surface.
- `PreToolUse` applies Galatea-specific deny/allow rules on every observed call;
  an unmatched call returns `defer` to Claude Code instead of locally recreating
  `default`, `acceptEdits`, `plan`, or `dontAsk` semantics.
- `bypassPermissions` fails at runtime construction unless the caller sets
  `allow_bypass_permissions=True` explicitly.
- Interactive/service approvals use the SDK-native `can_use_tool` callback or
  `permission_prompt_tool_name`; they are mutually exclusive and require
  `default`, `acceptEdits`, or `auto` mode. `PermissionRequest`
  hooks record request ID, scope, reason, persistence suggestions, and attribution.
- The default runtime remains non-interactive and read-only: `dontAsk`, exact MCP
  inspection allow rules, and generic mutation tools denied.

## Skill Boundary

`AgentSDKConfig.skills`, `plugins`, and `setting_sources` are passed directly to
`ClaudeAgentOptions`. Galatea does not create `Skill(name)` rules, inject the
`Skill` base tool, or change settings sources. SDK 0.2.136 performs those steps.

Repository Skills have tracked `.claude/skills/<name>` discovery paths. The local
plugin manifest remains available for callers that explicitly pass the repository
as an SDK plugin. `SkillRegistry` is suitable for `/skills` display and startup
preflight only; a successful preflight does not prove or grant runtime access.

## Direct Test Boundary

`agent.tools.executor` is an offline unit-test/CI harness. It accepts the exact
SDK `SdkMcpTool` objects used by the production MCP server and is intentionally
not exported from `agent.tools`. Formal tool execution always goes through the
SDK MCP lifecycle. MCP `ToolAnnotations` carry read-only, destructive,
idempotence, and open-world hints.

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
