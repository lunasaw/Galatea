import { test } from "node:test";
import assert from "node:assert/strict";
import { invalidatesNativeResources, nativeNotice } from "./native-notifications.ts";

test("native warnings are visible without mistaking requests or outbound calls for notifications", () => {
  const event = { event_id: "42", kind: "notification", direction: "runtime_to_host", method: "configWarning", payload: { params: { message: "disabled profile" } } };
  assert.deepEqual(nativeNotice(event)?.params, { message: "disabled profile" });
  assert.equal(nativeNotice({ ...event, direction: "host_to_runtime" }), undefined);
  assert.equal(nativeNotice({ ...event, kind: "request" }), undefined);
  assert.equal(nativeNotice({ ...event, method: "item/reasoning/textDelta" }), undefined);
});
test("cross-client resources invalidate on authoritative changes without refreshing for every token", () => {
  const event = { kind: "notification", direction: "runtime_to_host" };
  for (const method of ["account/updated", "project/changed", "thread/readState/changed", "thread/attachment/updated", "skills/changed",
    "account/rateLimits/updated", "thread/goal/updated", "thread/name/updated", "mcpServer/oauthLogin/completed",
    "externalAgentConfig/import/completed", "remoteControl/status/changed"])
    assert.equal(invalidatesNativeResources({ ...event, method }), true);
  assert.equal(invalidatesNativeResources({ ...event, method: "item/agentMessage/delta" }), false);
});

test("per-turn moderation metadata is diagnostic data, not a user-facing notice", () => {
  for (let index = 0; index < 8; index++) {
    const event = { event_id: `e:${index}`, kind: "notification", direction: "runtime_to_host",
      method: "turn/moderationMetadata", payload: { params: { threadId: "thread", turnId: "turn", metadata: { sequence: index } } } };
    assert.equal(nativeNotice(event), undefined);
    assert.equal(invalidatesNativeResources(event), false);
  }
});
