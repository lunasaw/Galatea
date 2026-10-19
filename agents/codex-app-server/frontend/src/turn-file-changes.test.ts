import test from "node:test";
import assert from "node:assert/strict";
import { diffLines, parseUnifiedDiff } from "./workbench-data";
import { toConversationMessages } from "./messages";
import type { Snapshot } from "./types";

test("line numbers and counts follow multiple hunks and header-looking code", () => {
  const diff = 'diff --git a/a.ts b/a.ts\n--- a/a.ts\n+++ b/a.ts\n@@ -23,2 +23,3 @@\n context\n--- code\n+++ code\n+extra\n@@ -40 +41 @@\n-old\n+new\n';
  const [file] = parseUnifiedDiff(diff);
  assert.deepEqual([file.additions, file.removals], [3, 2]);
  assert.deepEqual(diffLines(diff).filter((l) => l.kind === "add").map((l) => l.newLine), [24, 25, 41]);
  assert.deepEqual(diffLines(diff).filter((l) => l.kind === "remove").map((l) => l.oldLine), [24, 40]);
});

test("parses additions, removals, spaces and Git-quoted Unicode paths", () => {
  const diff = 'diff --git a/hello world.ts b/hello world.ts\n--- /dev/null\n+++ b/hello world.ts\n@@ -0,0 +1 @@\n+hello\n' +
    'diff --git "a/\\344\\270\\255.ts" "b/\\344\\270\\255.ts"\n--- "a/\\344\\270\\255.ts"\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n';
  assert.deepEqual(parseUnifiedDiff(diff).map((f) => [f.path, f.kind, f.additions, f.removals]), [
    ["hello world.ts", "add", 1, 0], ["中.ts", "delete", 0, 1],
  ]);
});

test("one card anchor per finished turn, including an interrupted tool-only turn", () => {
  const data: Snapshot = { epoch: "e", watermark: "e:9", next_page: null, coverage: {status: "complete"},
    turns: [{id: "one", status: "completed"}, {id: "two", status: "interrupted"}, {id: "live", status: "inProgress"}],
    messages: [
      {id: "a", role: "assistant", turn_id: "one", text: "Final", phase: "final_answer", first_seq: "1", last_seq: "2"},
      {id: "b", role: "assistant", turn_id: "one", text: "Later final", phase: "final_answer", first_seq: "3", last_seq: "4"},
      {id: "c", role: "assistant", turn_id: "live", text: "Writing", phase: "final_answer", first_seq: "7", last_seq: "8"},
    ], steps: [{id: "patch", type: "fileChange", turn_id: "two", first_seq: "5", last_seq: "6", status: "completed"}],
  };
  const cards = toConversationMessages(data).filter((r) => r.metadata?.custom?.fileChangesTurnId);
  assert.deepEqual(cards.map((r) => r.metadata?.custom?.fileChangesTurnId), ["one", "two"]);
  assert.equal(cards[0].id, "b");
});
