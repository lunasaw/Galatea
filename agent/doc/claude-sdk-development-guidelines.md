# Claude SDK Development Guidelines

Use the Claude Agent SDK as the foundation. Do not reimplement the SDK runtime,
agent loop, transcript store protocol, project agents, or tool transport.

## Required SDK Abstractions

- `ClaudeSDKClient` for model sessions.
- `ClaudeAgentOptions` for runtime configuration.
- `create_sdk_mcp_server` and `@tool` for in-process tools.
- SDK `AgentDefinition` for subagents.
- SDK `HookMatcher` and supported hook events.
- SDK `HookInput` and `HookJSONOutput` directly; do not add a parallel hook schema.
- SDK `SessionStore` protocol for transcript mirror/resume.
- SDK `skills` and `plugins` as the only runtime Skill authorization inputs.

## Permission Rules

- Prefer exact MCP tool names in `allowed_tools`.
- Keep generic mutation tools denied by default.
- Use `PreToolUse` hooks as the per-call safety boundary.
- Let Claude Code evaluate permission modes; Galatea rules return `defer` when
  they have no platform-specific allow/deny decision.
- Do not use `permission_mode="bypassPermissions"` for platform tasks. The
  runtime requires an explicit `allow_bypass_permissions=True` elevation.
- Use either SDK `can_use_tool` or `permission_prompt_tool_name` for approval,
  never a callback that silently converts `ask` to deny. Approval handlers are
  invalid in `dontAsk`, `plan`, and `bypassPermissions` modes.
- Do not treat planned tools as allowlisted current capabilities.

## Skill Rules

- Pass requested names in `AgentSDKConfig.skills`; do not add `Skill` or
  `Skill(name)` to `allowed_tools` locally.
- Pass local plugins explicitly in `AgentSDKConfig.plugins`.
- Keep repository Skills under `.claude/skills` (symlinks are acceptable) or an
  explicit plugin. `.codex/skills` alone is not a Claude runtime discovery source.
- Use `SkillRegistry.preflight()` only for UI/startup diagnostics. The SDK and
  bundled Claude Code CLI remain the discovery and authorization authority.

## Direct Execution And Workflow Rules

- Production calls use SDK MCP. `agent.tools.executor` is an offline test harness
  built from the same `SdkMcpTool` objects and is not a public runtime API.
- MCP tools must declare standard `ToolAnnotations`. Destructive or expensive
  tools additionally require an explicit approval design before registration.
- Workflow helpers record state, prerequisites, and evidence only. They do not
  dispatch LLMs, call stage handlers, retry jobs, or publish artifacts.

## Session Store Rules

`AgentSDKConfig.session_store` accepts only transcript stores with `append` and `load`.
Application state stores belong under `agent/state/` and are not SDK transcript stores.

## Business Tool Rules For Next Phase

- Implement platform actions as SDK MCP tools, not generic shell commands.
- Define schemas before model exposure.
- Add focused tests before runtime allowlisting.
- Keep expensive, destructive, or production-affecting actions behind approval.

## Validation

```bash
python -m unittest agent.test.test_sdk_audit_conformance
python -m unittest discover -s agent/test -p 'test_*.py'
```
