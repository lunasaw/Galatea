import { test } from "node:test";
import assert from "node:assert/strict";
import { transcriptEvent, type TranscriptState } from "./voice-transcript";

test("canonical history replaces duplicate flat speech and retains interleaved speaker order", () => {
  let state: TranscriptState = { canonical: false, lines: [] };
  const send = (suffix: string, params: object) => { state = transcriptEvent(state, "thread/realtime/" + suffix, params); };
  send("transcript/delta", { role: "user", delta: "hello" });
  send("item/started", { item: { id: "u", type: "transcriptSegment", role: "user", text: "hello" } });
  send("transcript/done", { role: "user", text: "hello" });
  send("item/started", { item: { id: "a", type: "transcriptSegment", role: "assistant", text: "" } });
  send("item/transcript/delta", { itemId: "a", delta: "hi" });
  send("item/completed", { item: { id: "u", type: "transcriptSegment", role: "user", text: "hello" } });
  assert.deepEqual(state.lines.map(({ id, text }) => ({ id, text })), [{ id: "u", text: "hello" }, { id: "a", text: "hi" }]);
});

test("flat-only runtime responses remain readable", () => {
  let state: TranscriptState = { canonical: false, lines: [] };
  state = transcriptEvent(state, "thread/realtime/transcript/delta", { role: "assistant", delta: "hel" });
  state = transcriptEvent(state, "thread/realtime/transcript/done", { role: "assistant", text: "hello" });
  assert.deepEqual(state.lines, [{ role: "assistant", text: "hello", done: true }]);
});
