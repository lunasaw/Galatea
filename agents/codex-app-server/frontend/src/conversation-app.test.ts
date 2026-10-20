import assert from "node:assert/strict";
import { test } from "node:test";
import { conversationApp } from "./conversation-app";
import type { Snapshot, Step } from "./types";

const step = (id: string, seq: string, detail = {}): Step => ({
  id, type: "mcpToolCall", first_seq: seq, last_seq: seq,
  detail: { server: "training", tool: "show_progress", arguments: { task_id: id },
    mcpAppUi: { resourceUri: "ui://training/progress" }, status: "completed", ...detail },
});
function state(steps: Step[]) {
  return { active: "chat", sessions: [{ id: "chat", thread_id: "native-chat", status: "running" }],
    snapshot: { steps } as Snapshot };
}
test("native tool metadata opens a view in the originating chat, with live result", () => {
  const result = { content: [], structuredContent: { progress: 3 } };
  const app = conversationApp(state([step("run", "1", { status: "inProgress", result })]));
  assert.equal(app?.session_id, "chat");
  assert.equal(app?.thread_id, "native-chat");
  assert.equal(app?.arguments.task_id, "run");
  assert.equal(app?.result, result);
  assert.equal(app?.activation_id, "run");
  const paged = state([]);
  paged.snapshot.app_step = step("older", "1");
  assert.equal(conversationApp(paged)?.activation_id, "older");
});
test("newest invocation wins independent of array order; failures do not replace it", () => {
  const app = conversationApp(state([step("latest", "10"), step("old", "2"),
    step("failed", "11", { result: { isError: true } })]));
  assert.equal(app?.activation_id, "latest");
});
test("history replay selects the latest App even when the retained App has the same sequence", () => {
  const old = { ...step("z-old", "10"), replay_order: 1 };
  const latest = { ...step("a-latest", "10"), replay_order: 3 };
  const replay = state([latest, old]);
  replay.snapshot.app_step = old;
  assert.equal(conversationApp(replay)?.arguments.task_id, "a-latest");
  replay.snapshot.steps = [old];
  replay.snapshot.app_step = latest;
  assert.equal(conversationApp(replay)?.activation_id, "a-latest");
});
test("plain tools, chat keywords, missing threads and remote HTML never open a view", () => {
  assert.equal(conversationApp(state([{ ...step("plain", "1"), type: "tool", text: "train model" }])), undefined);
  assert.equal(conversationApp(state([step("bad", "1", { mcpAppUi: { resourceUri: "https://external" } })])), undefined);
  assert.equal(conversationApp({ ...state([step("run", "1")]), active: "new-chat" }), undefined);
  assert.equal(conversationApp(state([])), undefined);
});
test("trusted native Provider result presents an MCP resource without a second model call", () => {
  const contentItems = [{ type: "inputText", text: JSON.stringify({ schema_version: "codex-agent.tools/v1",
    ok: true, data: { app_view: { server: "training", resource_uri: "ui://training/progress", arguments: { task_id: "t" } } } }) }];
  const call: Step = { ...step("native", "1"), type: "tool", detail: { success: true, contentItems } };
  const app = conversationApp(state([call]));
  assert.equal(app?.app.server, "training");
  assert.equal(app?.arguments.task_id, "t");
  assert.equal(app?.thread_id, "native-chat");
  assert.equal(conversationApp(state([{ ...call, success: false }])), undefined);
  assert.equal(conversationApp(state([{ ...call, detail: { contentItems: [{ type: "inputText", text: "null" }] } }])), undefined);
  assert.equal(conversationApp(state([{ ...call, detail: { contentItems: [{ type: "inputText", text: '{"app_view":{}}' }] } }])), undefined);
});
