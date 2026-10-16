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
