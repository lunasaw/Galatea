# Galatea Python Agent SDK Foundation

This document describes the Python-native foundation for Galatea agents. The current
phase intentionally excludes business-agent execution logic; the next phase can add
business tools on top of this base.

## SDK Mapping

| Claude Code / SDK concept | Galatea implementation |
| --- | --- |
| Query engine | `agent/core/sdk.py::GalateaSDKRuntime` wraps `ClaudeSDKClient`. |
| Options | `AgentSDKConfig` builds `ClaudeAgentOptions`. |
| Tools | `agent/tools/server.py` creates an in-process SDK MCP server. |
| Subagents | `agent/agents/definitions.py` uses SDK `AgentDefinition`. |
| Hooks | `agent/hooks/registry.py` converts local callbacks to SDK `HookMatcher`. |
| Permissions | `agent/policies/permission.py` plus `PreToolUse` hooks. |
| Sessions | SDK `SessionStore` for transcripts; `agent/state/store.py` for app metadata. |
| Structured output | SDK `output_format` and local schema validation. |

## Package Layout

```text
agent/
├── core/                 # SDK runtime and result collection
├── runtime.py            # high-level runtime facade
├── client.py             # high-level SDK client
├── commands/             # slash/natural command planning
├── tools/                # in-process SDK MCP tools and deterministic executor
├── schemas/              # shared and inspection schemas
├── state/                # application state helpers, not SDK transcript storage
├── workflows/            # generic workflow state machine and orchestrator
├── policies/             # budget, permission, and quality policies
├── hooks/                # SDK hook adapters and built-in safety hooks
├── agents/               # SDK AgentDefinition presets and registry
├── scripts/              # CLI entry points
└── doc/                  # current architecture and usage docs
```

## Current Capabilities

Current runtime capabilities are limited to SDK session orchestration, read-only platform
inspection tools, hooks, permission policy, structured output validation, command planning,
and generic state/workflow helpers. Stage-specific business execution is not implemented.

## Planned Business Integration

When adding business agents in the next phase:

1. Define a schema contract in `agent/schemas/`.
2. Implement a named SDK MCP tool in `agent/tools/`.
3. Add permission rules and hooks for the exact tool name.
4. Add tests for tool behavior, schema validation, and denial paths.
5. Wire the tool into runtime `allowed_tools` only after tests pass.
6. Keep expensive or mutating actions behind approval.

## Session Store Requirements

A Claude SDK transcript store must implement `append(key, entries)` and `load(key)`. Application
state stores in `agent/state/` are separate and cannot be used as SDK transcript stores.

## Unsupported Stage CLIs

`run_data_stage.py`, `run_training_stage.py`, and `run_inference_stage.py` intentionally return
structured `unsupported` results until business MCP tools are implemented.
