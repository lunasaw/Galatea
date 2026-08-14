# Galatea Agent Implementation Roadmap

> Current phase: SDK foundation only. Business agents are next-phase work.

## Completed Foundation

| Area | Status |
| --- | --- |
| SDK runtime | `GalateaSDKRuntime` builds `ClaudeAgentOptions` and uses `ClaudeSDKClient`. |
| MCP server | `galatea-platform` in-process SDK MCP server with five read-only inspection tools. |
| Permissions | Default-deny policy plus `PreToolUse` hook boundary. |
| Hooks | SDK-supported hook events only. |
| Agent definitions | SDK `AgentDefinition` presets and SDK-native registry. |
| Session boundary | SDK transcript store validation; app state kept separate. |
| Commands | Scoped prompt-command planning for runtime options. |
| Stage CLIs | Planned stage entry points return structured `unsupported`. |
| Tests | SDK audit conformance and offline unit tests. |

## Next Phase: Business Agent Integration

Add business capabilities incrementally:

1. Pick one stage and define its schema.
2. Implement one SDK MCP tool with narrow inputs and explicit side effects.
3. Add deterministic tests and denial-path tests.
4. Add tool-specific permission rules and approval behavior.
5. Expose the tool through runtime configuration.
6. Document current vs planned behavior.

## Guardrails

- No direct `mlflow.db` access.
- No generic Bash boundary for platform actions.
- No automatic long training, data writes, model alias changes, or production promotion.
- No test-set use for tuning or model selection.
- No hidden writes from planned stage CLIs.

## Validation

```bash
python -m unittest discover -s agent/test -p 'test_*.py'
```
