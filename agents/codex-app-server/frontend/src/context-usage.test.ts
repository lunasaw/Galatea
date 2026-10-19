import { test } from "node:test";
import assert from "node:assert/strict";
import { contextUsage, contextWarning } from "./context-usage";
import type { Snapshot } from "./types";
const snapshot = (usage: unknown): Snapshot => ({ epoch: "e", watermark: "e:1", next_page: null, coverage: { status: "complete" }, turns: [{ id: "turn", usage }] });
test("context details separate current occupancy and cumulative usage, including unknown capacity", () => {
  assert.deepEqual(contextUsage(snapshot({ last: { totalTokens: 204591 }, total: { totalTokens: 613805 }, modelContextWindow: 258400 })), { used: 204591, total: 613805, window: 258400, percent: 79 });
  assert.deepEqual(contextUsage(snapshot({ last: { totalTokens: 0 }, modelContextWindow: 1000 })), { used: 0, total: null, window: 1000, percent: 0 });
  for (const capacity of [null, undefined, 0, -1, NaN, Infinity]) assert.equal(contextUsage(snapshot({ last: { totalTokens: 50 }, modelContextWindow: capacity })).percent, null);
  assert.equal(contextUsage(snapshot({ last: { totalTokens: Infinity }, modelContextWindow: 1000 })).used, null);
  assert.equal(contextUsage(snapshot({ last: { totalTokens: 1400 }, modelContextWindow: 1000 })).percent, 100);
  assert.deepEqual(contextUsage(null), { used: null, total: null, window: null, percent: null });
});
test("context warnings describe capacity pressure without promising automatic compaction", () => {
  for (const percent of [null, 0, 79]) assert.equal(contextWarning(percent), "");
  assert.match(contextWarning(80), /占用较高/);
  assert.match(contextWarning(95), /接近容量/);
  assert.doesNotMatch(contextWarning(100), /自动压缩|正在压缩|压缩进度/);
});
