import { test } from "node:test";
import assert from "node:assert/strict";
import { toMessages, toolState } from "./messages.ts";
import type { PendingMessage, Snapshot, Step } from "./types.ts";
const step = (values: Partial<Step>): Step => ({
  id: "tool",
  type: "tool",
  first_seq: "9007199254740993",
  last_seq: "9007199254740994",
  tool: "query",
  ...values,
});
test("local messages retain attachments and reconcile by request identity, never text", () => {
  const input = [{ type: "image", url: "data:image/png;base64,AAAA" }, { type: "skill", name: "example", path: "/example/SKILL.md" }];
  const pending: PendingMessage[] = [{ id: "chat", key: "new", text: "same text", status: "sending", input }];
  const snapshot: Snapshot = { epoch: "e", watermark: "e:12", next_page: null, coverage: { status: "complete" },
    messages: [{ id: "request:old", role: "user", text: "same text", first_seq: "1", last_seq: "1" }],
    steps: [step({ id: "tool", last_seq: "9007199254740999" })],
  };
  const rows = toMessages(snapshot, pending);
  assert.deepEqual(rows.map((row) => row.id), ["request:old", "tool", "request:new"]);
  assert.deepEqual(rows.at(-1)?.content, [{ type: "text", text: "same text" }, { type: "image", image: input[0].url }]);
  assert.deepEqual(rows.at(-1)?.metadata?.custom?.nativeContent, input);
  snapshot.messages!.push({ id: "request:new", role: "user", text: "same text", first_seq: "9007199254741000", last_seq: "9007199254741000" });
  assert.deepEqual(toMessages(snapshot, pending).map((row) => row.id), ["request:old", "tool", "request:new"]);
  assert.equal(toMessages(snapshot, pending).at(-1)?.metadata?.custom?.sendStatus, undefined);
});
test("preserves decimal cursor ordering above Number precision and stable native identities", () => {
  const snapshot: Snapshot = {
    epoch: "e",
    watermark: "e:9007199254740998",
    next_page: null,
    coverage: { status: "complete" },
    messages: [
      {
        id: "final",
        role: "assistant",
        text: "final replaces deltas",
        first_seq: "9007199254740994",
        last_seq: "9007199254740998",
        turn_id: "t",
      },
      {
        id: "user",
        role: "user",
        text: "hello",
        first_seq: "9007199254740992",
        last_seq: "9007199254740992",
      },
    ],
    steps: [
      step({ success: false, result: { error: "denied" }, turn_id: "t" }),
    ],
    turns: [{ id: "t", status: "completed" }],
  };
  const messages = toMessages(snapshot);
  assert.deepEqual(
    messages.map((m) => m.id),
    ["user", "tool", "final"],
  );
  assert.equal(messages.filter((m) => m.id === "final").length, 1);
  assert.notEqual(typeof messages[1].content, "string");
  assert.deepEqual(messages[1].content[0], {
    type: "tool-call",
    toolCallId: "tool",
    toolName: "query",
    argsText: "{}",
    result: { error: "denied" },
    isError: true,
  });
});
test("unknown tool observations never imply successful completion", () => {
  assert.equal(toolState(step({})), "unknown");
  assert.equal(toolState(step({ status: "inProgress" })), "running");
  assert.equal(
    toolState(step({ status: "completed", success: false })),
    "failed",
  );
  assert.equal(toolState(step({ success: true })), "complete");
});

test("legacy moderation warning projections stay out of chat while real warnings and errors remain", () => {
  const snapshot: Snapshot = { epoch: "e", watermark: "e:12", next_page: null, coverage: { status: "complete" },
    steps: [
      ...Array.from({ length: 8 }, (_, index) => step({ id: `moderation-${index}`, type: "warning",
        detail: { method: "turn/moderationMetadata", metadata: { sequence: index } } })),
      step({ id: "warning", type: "warning", detail: { message: "Action needs attention" } }),
      step({ id: "guardian", type: "warning", detail: { method: "guardianWarning", message: "Approval unavailable" } }),
      step({ id: "error", type: "error", detail: { message: "Command failed" } }),
    ] };
  assert.deepEqual(toMessages(snapshot).map((message) => message.id), ["warning", "guardian", "error"]);
  assert.equal(snapshot.steps?.length, 11, "presentation filtering preserves diagnostic history");
});

const reasoningSnapshot = (steps: Step[], status = "inProgress"): Snapshot => ({
  epoch: "e",
  watermark: "e:30",
  next_page: null,
  coverage: { status: "complete" },
  steps,
  turns: [{ id: "t", status }],
});
const reasoningStep = (values: Partial<Step> = {}): Step =>
  step({
    id: "item:thread:t:r",
    type: "reasoning",
    turn_id: "t",
    first_seq: "2",
    last_seq: "9",
    status: "inProgress",
    ...values,
  });

test("streams summary paragraphs in index order using a stable native item", () => {
  const snapshot = reasoningSnapshot([
    reasoningStep({
      summary_1: "Second paragraph",
      summary_0: "First paragraph",
      detail: { summary: ["stale initial summary"], content: ["private"] },
    }),
  ]);
  const [message] = toMessages(snapshot);
  assert.equal(message.id, "item:thread:t:r");
  assert.deepEqual(message.status, { type: "running" });
  assert.deepEqual(message.content, [
    { type: "reasoning", text: "First paragraph\n\nSecond paragraph" },
  ]);
});

test("completed summary replaces deltas, carries observed timing, and survives replay", () => {
  const snapshot = reasoningSnapshot([
    reasoningStep({
      status: "completed",
      summary_0: "partial",
      started_at: "2026-10-15T01:00:00Z",
      completed_at: "2026-10-15T01:00:04Z",
      detail: { summary: ["Final", "Second"], content: ["private"] },
    }),
  ]);
  const [message] = toMessages(snapshot);
  assert.deepEqual(message.status, { type: "complete", reason: "stop" });
  assert.deepEqual(message.content, [
    {
      type: "reasoning",
      text: "Final\n\nSecond",
      timing: {
        startedAt: Date.parse("2026-10-15T01:00:00Z"),
        completedAt: Date.parse("2026-10-15T01:00:04Z"),
      },
    },
  ]);
  assert.deepEqual(toMessages(JSON.parse(JSON.stringify(snapshot))), [message]);
});

test("reconciles legacy delta and completed rows without duplicate panels or invented timing", () => {
  const snapshot = reasoningSnapshot([
    reasoningStep({
      status: "completed",
      detail: { summary: ["Final summary"] },
    }),
    step({
      id: "legacy",
      type: "item/reasoning/summaryTextDelta",
      turn_id: "t",
      first_seq: "3",
      last_seq: "8",
      text: "Partial summary",
      detail: { itemId: "r", threadId: "thread" },
    }),
  ]);
  assert.equal(toMessages(snapshot).length, 1);
  assert.deepEqual(toMessages(snapshot)[0].content, [
    { type: "reasoning", text: "Final summary" },
  ]);
  snapshot.steps = snapshot.steps!.slice(1);
  assert.equal(toMessages(snapshot)[0].id, "item:thread:t:r");
  assert.deepEqual(toMessages(snapshot)[0].content, [
    { type: "reasoning", text: "Partial summary" },
  ]);
});

test("ends a missing-completion preview when the same turn advances, or marks interruption", () => {
  const snapshot = reasoningSnapshot(
    [reasoningStep({ summary_0: "Public summary" })],
    "interrupted",
  );
  assert.deepEqual(toMessages(snapshot)[0].status, {
    type: "incomplete",
    reason: "cancelled",
  });
  snapshot.turns![0].status = "inProgress";
  snapshot.messages = [
    {
      id: "answer",
      role: "assistant",
      text: "Answer",
      turn_id: "t",
      first_seq: "10",
      last_seq: "11",
    },
  ];
  assert.deepEqual(toMessages(snapshot)[0].status, {
    type: "complete",
    reason: "stop",
  });
  assert.deepEqual(
    toMessages(snapshot).map((m) => m.id),
    ["item:thread:t:r", "answer"],
  );
});

test("never presents private reasoning or empty summaries as reasoning panels", () => {
  const snapshot = reasoningSnapshot([
    reasoningStep({ detail: { content: ["private"], summary: [] } }),
    step({ type: "item/reasoning/textDelta", text: "private delta" }),
  ]);
  assert.deepEqual(toMessages(snapshot), []);
});

test("native history hydration preserves interleaved user, reasoning, tools and answers", () => {
  const result = toMessages({
    epoch: "e",
    watermark: "e:100",
    next_page: null,
    coverage: { status: "complete" },
    messages: [
      {
        id: "user",
        role: "user",
        text: "question",
        turn_id: "t",
        first_seq: "100",
        last_seq: "100",
        replay_order: 1,
      },
      {
        id: "answer",
        role: "assistant",
        text: "answer",
        turn_id: "t",
        first_seq: "100",
        last_seq: "100",
        replay_order: 4,
      },
    ],
    steps: [
      {
        id: "reasoning",
        type: "reasoning",
        turn_id: "t",
        first_seq: "100",
        last_seq: "100",
        replay_order: 2,
        status: "completed",
        detail: { summary: ["public summary"] },
      },
      {
        id: "tool",
        type: "commandExecution",
        turn_id: "t",
        first_seq: "100",
        last_seq: "100",
        replay_order: 3,
        status: "completed",
        detail: { command: "pwd" },
      },
    ],
    turns: [{ id: "t", status: "completed" }],
  });
  assert.deepEqual(
    result.map((m) => m.id),
    ["user", "reasoning", "tool", "answer"],
  );
});

test("groups process items per turn around steered input and keeps only final answer actions", async () => {
  const { toConversationMessages } = await import("./messages.ts");
  const snapshot: Snapshot = {
    epoch: "e",
    watermark: "e:20",
    next_page: null,
    coverage: { status: "complete" },
    messages: [
      {
        id: "u",
        role: "user",
        text: "task",
        turn_id: "t",
        first_seq: "1",
        last_seq: "1",
      },
      {
        id: "c",
        role: "assistant",
        phase: "commentary",
        status: "completed",
        text: "checking",
        turn_id: "t",
        first_seq: "2",
        last_seq: "2",
      },
      {
        id: "s",
        role: "user",
        text: "steer",
        turn_id: "t",
        first_seq: "5",
        last_seq: "5",
      },
      {
        id: "f",
        role: "assistant",
        phase: "final_answer",
        text: "done",
        turn_id: "t",
        first_seq: "8",
        last_seq: "9",
      },
    ],
    steps: [
      step({
        id: "cmd",
        type: "commandExecution",
        turn_id: "t",
        first_seq: "3",
        last_seq: "4",
        status: "completed",
      }),
      step({
        id: "plan",
        type: "turn/plan/updated",
        turn_id: "t",
        first_seq: "6",
        last_seq: "7",
        detail: { plan: [] },
      }),
    ],
    turns: [{ id: "t", status: "completed" }],
  };
  const rows = toConversationMessages(snapshot);
  assert.deepEqual(
    rows.map((r) => r.id),
    ["u", "activity:c", "s", "activity:plan", "f"],
  );
  assert.equal(rows[1].metadata?.custom?.itemCount, 2);
  assert.equal(rows[1].content.length, 2);
  assert.equal(rows[4].metadata?.custom?.final, true);
  assert.deepEqual(
    toConversationMessages(JSON.parse(JSON.stringify(snapshot))),
    rows,
  );
  snapshot.turns![0].status = "inProgress";
  assert.deepEqual(toMessages(snapshot).find((r) => r.id === "c")?.status, {
    type: "complete",
    reason: "stop",
  });
});

test("legacy final inference never turns interrupted commentary into success", async () => {
  const { toConversationMessages } = await import("./messages.ts");
  const snapshot: Snapshot = {
    epoch: "e",
    watermark: "e:3",
    next_page: null,
    coverage: { status: "complete" },
    messages: [
      {
        id: "m",
        role: "assistant",
        text: "working",
        turn_id: "t",
        first_seq: "1",
        last_seq: "2",
      },
    ],
    turns: [{ id: "t", status: "interrupted" }],
  };
  assert.equal(
    toConversationMessages(snapshot)[0].metadata?.custom?.activity,
    true,
  );
  assert.equal(
    toolState(
      step({
        type: "commandExecution",
        status: "completed",
        detail: { exitCode: 1 },
      }),
    ),
    "failed",
  );
  assert.equal(toolState(step({ status: "declined" })), "failed");
});
