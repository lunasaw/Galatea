import { test } from "node:test";
import assert from "node:assert/strict";
import { readTurns, rollbackRequest } from "./thread-history.ts";

test("rollback uses authoritative full history including older turns and excludes the selected turn", async () => {
  const history = await readTurns(
    {
      async nativeCall(method, params) {
        assert.equal(method, "thread/read");
        assert.deepEqual(params, { threadId: "t", includeTurns: true });
        return {
          thread: {
            turns: Array.from({ length: 220 }, (_, i) => ({
              id: String(i),
              status: "completed",
              items: [
                {
                  type: "userMessage",
                  content: [{ type: "text", text: `prompt ${i}` }],
                },
              ],
            })),
          },
        };
      },
    },
    "t",
  );
  assert.equal(history.turns[0].text, "prompt 0");
  assert.deepEqual(rollbackRequest("t", history, "20"), {
    method: "thread/rollback",
    params: { threadId: "t", numTurns: 200 },
  });
  assert.deepEqual(
    rollbackRequest("t", { ...history, historyMode: "paginated" }, "20"),
    { method: "thread/revert", params: { threadId: "t", beforeTurnId: "20" } },
  );
  assert.equal(rollbackRequest("t", history, "219").params.numTurns, 1);
  assert.throws(() => rollbackRequest("t", history, "missing"));
  history.turns.at(-1)!.status = "inProgress";
  assert.throws(() => rollbackRequest("t", history, "20"));
});
