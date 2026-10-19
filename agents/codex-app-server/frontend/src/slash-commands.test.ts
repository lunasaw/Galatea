import { test } from "node:test";
import assert from "node:assert/strict";
import { contextUsedPercent, exactSlashCommand, filterSlashCommands } from "./slash-commands";
import type { Snapshot } from "./types";

test("desktop commands keep the reference order and search Chinese labels and English names", () => {
  assert.deepEqual(filterSlashCommands("/").slice(0, 11).map((row) => row.name),
    ["mcp", "usage", "aside", "share", "fork", "init", "compact", "feedback", "archive", "reasoning", "model"]);
  assert.deepEqual(filterSlashCommands("/压缩").map((row) => row.name), ["compact"]);
  assert.deepEqual(filterSlashCommands("/COMPACT").map((row) => row.name), ["compact"]);
  for (const text of ["hello /mcp", "/mcp ", "/tmp/file", "//", "/unknown", "普通文字"]) assert.deepEqual(filterSlashCommands(text), []);
  assert.equal(exactSlashCommand(" /ARCHIVE ")?.name, "archive");
  assert.equal(exactSlashCommand("/init more"), undefined);
});
test("context usage uses the last native context, not cumulative billing tokens", () => {
  const snapshot = (usage: unknown): Snapshot => ({ epoch: "e", watermark: "e:1", next_page: null,
    coverage: { status: "complete" }, turns: [{ id: "old", usage: { last: { totalTokens: 1 }, modelContextWindow: 100 } }, { id: "new", usage }, { id: "no-usage" }] });
  assert.equal(contextUsedPercent(snapshot({ last: { totalTokens: 810 }, total: { totalTokens: 90000 }, modelContextWindow: 1000 })), 81);
  assert.equal(contextUsedPercent(snapshot({ last: { totalTokens: 1200 }, modelContextWindow: 1000 })), 100);
  assert.equal(contextUsedPercent(snapshot({ last: { totalTokens: 0 }, modelContextWindow: 1000 })), 0);
  for (const usage of [{}, { last: { totalTokens: -1 }, modelContextWindow: 100 }, { last: { totalTokens: 10 }, modelContextWindow: 0 }]) assert.equal(contextUsedPercent(snapshot(usage)), null);
  assert.equal(contextUsedPercent(null), null);
});
