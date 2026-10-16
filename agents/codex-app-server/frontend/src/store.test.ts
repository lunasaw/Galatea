import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { ConsoleStore } from "./store.ts";
import type { Snapshot } from "./types.ts";
const originalFetch = globalThis.fetch;
const memory = new Map<string, string>();
Object.defineProperty(globalThis, "localStorage", {
  value: {
    getItem: (k: string) => memory.get(k) || null,
    setItem: (k: string, v: string) => memory.set(k, v),
    removeItem: (k: string) => memory.delete(k),
  },
  configurable: true,
});
const json = (value: unknown) =>
  new Response(JSON.stringify(value), {
    headers: { "content-type": "application/json" },
  });
const snapshot = (id: string): Snapshot => ({
  epoch: "e",
  watermark: "e:10",
  next_page: null,
  coverage: { status: "complete" },
  messages: [{ id, role: "user", text: id, first_seq: "1", last_seq: "1" }],
  steps: [],
  turns: [],
});
const stores: ConsoleStore[] = [];
const make = () => {
  const s = new ConsoleStore();
  stores.push(s);
  return s;
};
afterEach(() => {
  stores.forEach((s) => s.dispose());
  stores.length = 0;
  globalThis.fetch = originalFetch;
  memory.clear();
});
test("lost send response retries with identical request body and idempotency key", async () => {
  const s = make();
  s.patch({ active: "a", sessions: [{ id: "a", status: "ready" }] });
  const calls: RequestInit[] = [];
  globalThis.fetch = async (path, init) => {
    if (String(path).endsWith("/messages")) {
      calls.push(init!);
      if (calls.length === 1) throw new TypeError("network lost");
      return json({ status: "accepted" });
    }
    return json([{ id: "a", status: "ready" }]);
  };
  await s.send("once");
  assert.equal(s.canRetry, true);
  await s.retrySend();
  assert.equal(s.canRetry, false);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].body, calls[1].body);
  assert.deepEqual(calls[0].headers, calls[1].headers);
  const body = JSON.parse(calls[0].body as string);
  assert.equal(
    body.request_id,
    (calls[0].headers as Record<string, string>)["Idempotency-Key"],
  );
});
test("late snapshot of a previously selected session cannot replace the new session", async () => {
  const s = make();
  let release!: (r: Response) => void;
  globalThis.fetch = async (path, init) => {
    const url = String(path);
    if (url.endsWith("/status")) return json({ epoch: "e", host_ready: true });
    if (url.endsWith("/sessions"))
      return json([
        { id: "a", status: "ready" },
        { id: "b", status: "ready" },
      ]);
    if (url.includes("/a/snapshot"))
      return new Promise((resolve) => {
        release = resolve;
      });
    if (url.includes("/b/snapshot")) return json(snapshot("b"));
    return new Promise((_, reject) =>
      init?.signal?.addEventListener("abort", () =>
        reject(new DOMException("aborted", "AbortError")),
      ),
    );
  };
  const old = s.select("a");
  await new Promise((resolve) => setImmediate(resolve));
  await s.select("b");
  release(json(snapshot("a")));
  await old;
  assert.equal(s.state.active, "b");
  assert.equal(s.state.snapshot?.messages?.[0].id, "b");
});
test("historical pagination uses a fixed watermark and excludes live tool projections", async () => {
  const s = make();
  s.patch({ active: "a", snapshot: snapshot("latest") });
  const urls: string[] = [];
  globalThis.fetch = async (path) => {
    const url = String(path);
    urls.push(url);
    return json(
      url.includes("until=")
        ? { ...snapshot("latest"), next_page: "opaque" }
        : {
            ...snapshot("old"),
            steps: [{ id: "tool" }],
            turns: [{ id: "turn" }],
          },
    );
  };
  await s.older();
  assert.ok(urls[0].includes("until=e%3A10"));
  assert.ok(urls[1].includes("page=opaque"));
  assert.equal(s.state.history, true);
  assert.deepEqual(s.state.snapshot?.steps, []);
  assert.deepEqual(s.state.snapshot?.turns, []);
  assert.equal(s.state.older, false);
});

test("model and plan changes bind to the selected thread and retain state on rejection", async () => {
  const s = make();
  s.patch({
    active: "a",
    sessions: [{ id: "a", thread_id: "native-a", status: "ready" }],
    preferences: { model: "model-a", effort: "medium", mode: "default" },
  });
  let params: any;
  globalThis.fetch = async (_, init) => {
    params = JSON.parse(init!.body as string);
    return json({});
  };
  assert.equal(
    await s.updateComposerSettings({
      model: "model-b",
      effort: "high",
      mode: "plan",
    }),
    true,
  );
  assert.equal(params.method, "thread/settings/update");
  assert.equal(params.params.threadId, "native-a");
  assert.deepEqual(params.params.collaborationMode, {
    mode: "plan",
    settings: {
      model: "model-b",
      reasoning_effort: "high",
      developer_instructions: null,
    },
  });
  assert.equal(s.composerSettings.mode, "plan");
  globalThis.fetch = async () =>
    new Response('{"error":"invalid-model"}', { status: 400 });
  assert.equal(await s.updateComposerSettings({ model: "unavailable" }), false);
  assert.equal(s.composerSettings.model, "model-b");
  assert.equal(s.state.settingsBusy, false);
});

test("retry preserves original model options even after settings change", async () => {
  const s = make();
  s.patch({
    active: "a",
    sessions: [
      {
        id: "a",
        thread_id: "native-a",
        status: "ready",
        settings: {
          model: "model-a",
          effort: "high",
          collaborationMode: {
            mode: "plan",
            settings: { model: "model-a", reasoning_effort: "high" },
          },
        },
      },
    ],
    preferences: { model: "model-a", effort: "high", mode: "plan" },
  });
  const calls: any[] = [];
  globalThis.fetch = async (path, init) => {
    if (String(path).endsWith("/messages")) {
      calls.push(JSON.parse(init!.body as string));
      if (calls.length === 1) throw new TypeError("network lost");
      return json({ status: "accepted" });
    }
    return json([{ id: "a", thread_id: "native-a", status: "ready" }]);
  };
  await s.send("run once");
  s.patch({
    preferences: { model: "model-b", effort: "low", mode: "default" },
  });
  await s.retrySend();
  assert.equal(calls[0].options.model, "model-a");
  assert.equal(calls[0].options.collaborationMode.mode, "plan");
  assert.deepEqual(calls[0], calls[1]);
});

test("switching back to a thread refreshes its active turn before composing", async () => {
  const s = make();
  s.patch({ sessions: [{ id: "a", status: "ready", thread_id: "native-a" }] });
  globalThis.fetch = async (path, init) => {
    const url = String(path);
    if (url.endsWith("/status")) return json({ epoch: "e", host_ready: true });
    if (url.endsWith("/sessions"))
      return json([
        {
          id: "a",
          status: "running",
          thread_id: "native-a",
          active_turn_id: "turn-2",
        },
      ]);
    if (url.endsWith("/snapshot")) return json(snapshot("a"));
    return new Promise((_, reject) =>
      init?.signal?.addEventListener("abort", () =>
        reject(new DOMException("aborted", "AbortError")),
      ),
    );
  };
  await s.select("a");
  assert.equal(s.state.sessions[0].active_turn_id, "turn-2");
  assert.equal(s.state.sessions[0].status, "running");
});

test("new-chat preferences do not override another existing thread", () => {
  const s = make();
  s.patch({
    preferences: { model: "draft-model", effort: "high", mode: "plan" },
    sessions: [{ id: "existing", status: "ready" }],
  });
  assert.equal(s.composerSettings.mode, "plan");
  s.patch({ active: "existing" });
  assert.equal(s.composerSettings.mode, "default");
  assert.equal(s.composerSettings.model, "");
});
