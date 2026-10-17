import assert from "node:assert/strict";
import test from "node:test";
import { agentActivities, agentStatus, agentTitle, mergeAgentTurns } from "./subagent-data";
import type { Step } from "./types";

test("agent titles use native task paths and names before opaque IDs", () => {
  const thread = { id: "child-123", agentNickname: "Singer", source: { subAgent: { thread_spawn: { agent_path: "/root/cli_inspect" } } } };
  assert.equal(agentTitle(thread), "Cli inspect");
  assert.equal(agentTitle({ ...thread, name: "检查 CLI" }), "检查 CLI");
  assert.equal(agentTitle({ id: "child-123", agentNickname: "Singer" }), "Singer");
});

test("active native state wins over old completed activity and tracks attention", () => {
  assert.deepEqual(agentStatus({ status: { type: "active", activeFlags: [] } }, [], { status: "completed" }), { label: "运行中", running: true });
  assert.equal(agentStatus({ status: { type: "active", activeFlags: ["waitingOnUserInput"] } }).label, "需要你处理");
  assert.equal(agentStatus({ status: { type: "idle" } }, [{ status: "completed" }]).label, "已完成");
  assert.equal(agentStatus({}, [{ status: "failed" }]).label, "失败");
  assert.equal(agentStatus({}, [{ status: "interrupted" }]).label, "已中断");
});

test("native activity keeps task and path across interaction and completion events", () => {
  const step = (type: string, detail: unknown): Step => ({ id: "s", type, detail, first_seq: "1", last_seq: "1" });
  const rows = agentActivities([
    step("subAgentActivity", { agentThreadId: "a", agentPath: "/root/inspect", kind: "started" }),
    step("collabAgentToolCall", { receiverThreadIds: ["a"], prompt: "检查 CLI", agentsStates: { a: { status: "completed", message: "完成" } } }),
    step("subAgentActivity", { agentThreadId: "a", kind: "interacted" }),
  ]);
  assert.deepEqual(rows.get("a"), { path: "/root/inspect", status: "completed", prompt: "检查 CLI", message: "完成" });
});

test("live turn replacement preserves loaded history and chronological order", () => {
  const previous = [{ id: "old", startedAt: 10 }, { id: "current", startedAt: 20, status: "inProgress" }];
  const latest = [{ id: "current", startedAt: 20, status: "completed", items: ["result"] }, { id: "next", startedAt: 30 }];
  const merged = mergeAgentTurns(previous, latest);
  assert.deepEqual(merged.map(t => t.id), ["old", "current", "next"]);
  assert.equal(merged[1].status, "completed");
  assert.deepEqual(merged[1].items, ["result"]);
  assert.equal(previous[1].status, "inProgress");
});
