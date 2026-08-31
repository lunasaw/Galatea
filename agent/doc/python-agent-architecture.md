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
| Hooks | `agent/hooks/registry.py` collects SDK callbacks and SDK `HookMatcher` directly. |
| Permissions | SDK modes/approval handlers plus Galatea allow/deny `PreToolUse` rules. |
| Skills | SDK `skills`/`plugins`; `SkillRegistry` is display/preflight only. |
| Sessions | SDK `SessionStore` for transcripts; `agent/state/store.py` for app metadata. |
| Structured output | SDK `output_format` and local schema validation. |

## Package Layout

```text
agent/
├── core/                 # SDK runtime and result collection
├── runtime.py            # high-level runtime facade
├── client.py             # high-level SDK client
├── tools/                # in-process SDK MCP tools; offline executor is test-only
├── schemas/              # shared and inspection schemas
├── state/                # application state helpers, not SDK transcript storage
├── workflows/            # deterministic workflow state/evidence only
├── policies/             # budget, permission, and quality policies
├── hooks/                # SDK-native callbacks and built-in safety hooks
├── agents/               # SDK AgentDefinition presets and registry
├── scripts/              # CLI entry points
└── doc/                  # current architecture and usage docs
```

## Current Capabilities

Current runtime capabilities are limited to SDK session orchestration, read-only platform
inspection tools, hooks, permission policy, structured output validation, and generic
state/evidence helpers. Prompt commands and stage-specific business execution are not
implemented in the foundation layer.

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
