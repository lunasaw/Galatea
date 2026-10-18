import assert from "node:assert/strict";
import test from "node:test";
import { reviewFiles, reviewTurns } from "./workbench-data";
import type { Snapshot, Step } from "./types";

const step = (turn: string, id: string, detail: unknown): Step => ({
  id,
  turn_id: turn,
  type: "fileChange",
  detail,
  first_seq: "1",
  last_seq: "2",
});
const snapshot = (steps: Step[]): Snapshot => ({
  epoch: "test",
  watermark: "1",
  next_page: null,
  coverage: { status: "complete" },
  steps,
});

test("review keeps changes isolated by turn and excludes diff headers from counts", () => {
  const data = snapshot([
    step("one", "a", {
      changes: [
        {
          path: "/repo/a.ts",
          diff: "--- a/a.ts\n+++ b/a.ts\n@@ -1 +1 @@\n-old\n+new",
        },
      ],
    }),
    step("two", "b", {
      changes: [{ path: "/repo/a.ts", diff: "-new\n+later" }],
    }),
    step("one", "c", { changes: [{ path: "/repo/a.ts", diff: "+extra" }] }),
  ]);
  assert.deepEqual(reviewTurns(data), [{ id: "one" }, { id: "two" }]);
  const first = reviewFiles(data, "one");
  assert.equal(first.length, 1);
  assert.equal(first[0].additions, 2);
  assert.equal(first[0].removals, 1);
  assert.doesNotMatch(first[0].diff, /later/);
  assert.equal(reviewFiles(data, "two")[0].diff, "-new\n+later");
});

test("review tolerates incomplete observations and does not borrow another turn", () => {
  const data = snapshot([
    step("one", "a", null),
    step("two", "b", { changes: [null, { path: "a" }, { diff: "+b" }] }),
  ]);
  assert.deepEqual(reviewTurns(data), []);
  assert.deepEqual(reviewFiles(data, "one"), []);
  assert.deepEqual(reviewFiles(null, undefined), []);
});

test("uses the last aggregate diff as net changes, including a reverted empty turn", () => {
  const data = snapshot([
    step("one", "a", { changes: [{ path: "a.ts", diff: "-old\n+middle" }] }),
    step("one", "b", { changes: [{ path: "a.ts", diff: "-middle\n+new" }] }),
    { ...step("one", "diff", { diff: "diff --git a/a.ts b/a.ts\n--- a/a.ts\n+++ b/a.ts\n@@ -1 +1 @@\n-old\n+new\n" }), type: "turnDiff" },
  ]);
  assert.equal(reviewFiles(data, "one")[0].additions, 1);
  assert.equal(reviewFiles(data, "one")[0].removals, 1);
  data.steps!.push({ ...step("one", "empty", { diff: "" }), type: "turnDiff", last_seq: "10" });
  assert.deepEqual(reviewFiles(data, "one"), []);
});

test("excludes rejected patches and counts native add/delete content", () => {
  const data = snapshot([
    { ...step("one", "fail", { changes: [{ path: "a", diff: "-a\n+b" }] }), status: "failed" },
    step("one", "add", { status: "completed", changes: [{ path: "added", kind: { type: "add" }, diff: "hello\nworld\n" }] }),
    step("one", "delete", { status: "completed", changes: [{ path: "deleted", kind: { type: "delete" }, diff: "gone\n" }] }),
  ]);
  assert.deepEqual(reviewFiles(data, "one").map(({path, additions, removals}) => ({path, additions, removals})), [
    {path: "added", additions: 2, removals: 0}, {path: "deleted", additions: 0, removals: 1},
  ]);
});
