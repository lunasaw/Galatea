# Claude SDK Development Guidelines

Use the Claude Agent SDK as the foundation. Do not reimplement the SDK runtime,
agent loop, transcript store protocol, project agents, or tool transport.

## Required SDK Abstractions

- `ClaudeSDKClient` for model sessions.
- `ClaudeAgentOptions` for runtime configuration.
- `create_sdk_mcp_server` and `@tool` for in-process tools.
- SDK `AgentDefinition` for subagents.
- SDK `HookMatcher` and supported hook events.
- SDK `SessionStore` protocol for transcript mirror/resume.

## Permission Rules

- Prefer exact MCP tool names in `allowed_tools`.
- Keep generic mutation tools denied by default.
- Use `PreToolUse` hooks as the per-call safety boundary.
- Do not use `permission_mode="bypassPermissions"` for platform tasks.
- Do not treat planned tools as allowlisted current capabilities.

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
