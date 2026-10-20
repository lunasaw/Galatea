import assert from "node:assert/strict";
import { test } from "node:test";
import { goalReader } from "./goal-reader";

test("disabled Goals stop repeated native requests until a new reader revision", async () => {
  let calls = 0;
  let enabled = false;
  const nativeRead = async () => {
    calls++;
    if (!enabled) throw new Error("422 · goals feature is disabled");
    return { goal: { objective: "finish" } };
  };
  const read = goalReader(nativeRead);
  assert.deepEqual(await read(), { goal: null });
  assert.deepEqual(await read(), { goal: null });
  assert.equal(calls, 1);
  enabled = true;
  assert.deepEqual(await goalReader(nativeRead)(), { goal: { objective: "finish" } });
  assert.equal(calls, 2);
});

test("empty goals and transient failures remain observable on the next refresh", async () => {
  let calls = 0;
  const read = goalReader(async () => {
    calls++;
    if (calls === 1) throw new Error("connection lost");
    return { goal: calls === 2 ? null : { objective: "finish" } };
  });
  await assert.rejects(read(), /connection lost/);
  assert.deepEqual(await read(), { goal: null });
  assert.deepEqual(await read(), { goal: { objective: "finish" } });
});
