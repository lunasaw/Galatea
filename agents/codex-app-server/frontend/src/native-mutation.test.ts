import { test } from "node:test";
import assert from "node:assert/strict";
import { NativeMutation } from "./native-mutation";

test("an uncertain asynchronous reply retries the exact request even after its turn ends", async () => {
  const original = globalThis.fetch; const calls: RequestInit[] = [];
  try {
    globalThis.fetch = async (_, init) => {
      calls.push(init!);
      if (calls.length === 1) throw new Error("response lost");
      return new Response("{}");
    };
    const mutation = new NativeMutation();
    await assert.rejects(mutation.run("turn/steer", { threadId: "t", expectedTurnId: "a", input: [] }, "reply"));
    await mutation.run("turn/start", { threadId: "t", input: [] }, "reply");
    assert.deepEqual(calls[0], calls[1]);
  } finally { globalThis.fetch = original; }
});

test("a definite rejection allows a new request identity after correcting runtime state", async () => {
  const original = globalThis.fetch; const calls: RequestInit[] = [];
  try {
    globalThis.fetch = async (_, init) => {
      calls.push(init!); return new Response(calls.length === 1 ? '{"error":"rejected"}' : "{}", { status: calls.length === 1 ? 400 : 200 });
    };
    const mutation = new NativeMutation();
    await assert.rejects(mutation.run("thread/queue/add", { threadId: "t" }, "reply"));
    await mutation.run("thread/queue/add", { threadId: "t" }, "reply");
    assert.notDeepEqual(calls[0]?.headers, calls[1]?.headers);
  } finally { globalThis.fetch = original; }
});
