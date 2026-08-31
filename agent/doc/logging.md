# Agent Logging

The current agent foundation logs model requests/responses, hook activity, tool calls, and
runtime validation details. Business-stage audit events should be introduced with the
corresponding next-phase tools.

## Model Logs

`GalateaRuntime.query()` logs request and response metadata as compact JSON records using
`MODEL_REQUEST` and `MODEL_RESPONSE` log messages.

## Hook Logs

Built-in hooks emit `GALATEA_HOOK` records with event name, session id, agent type, tool name,
and tool use id.

## Tool Logs

Use SDK message collection in `SDKRunResult.tool_calls` for deterministic summaries. Store large
raw outputs as artifacts or service-side logs; keep model context compact.

## Sensitive Data

Never log tokens, passwords, object-store keys, private endpoints, labels, or sensitive samples.
Business tools added later must redact or reference raw artifacts by URI instead of copying raw
payloads into model-visible context.
