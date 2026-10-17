import { test } from "node:test";
import assert from "node:assert/strict";
import { FileSearchSession, fileMatchPath } from "./file-search.ts";

const tick = () => new Promise((resolve) => setImmediate(resolve));
test("closing during native search startup waits before stopping and suppresses late updates", async () => {
  let release!: () => void;
  let receive!: (params: Record<string, any>) => void;
  const calls: string[] = [];
  const results: unknown[] = [];
  let unsubscribed = false;
  const session = new FileSearchSession({
    observe: async (_method, callback) => { receive = callback; return () => { unsubscribed = true; }; },
    call: async (method) => {
      calls.push(method);
      if (method.endsWith("Start")) await new Promise<void>((resolve) => { release = resolve; });
    },
  }, "search-a", ["/workspace"], (files) => results.push(files), (error) => assert.fail(String(error)));
  session.update("a");
  await tick();
  const closing = session.close();
  assert.deepEqual(calls, ["fuzzyFileSearch/sessionStart"]);
  receive({ sessionId: "search-a", query: "a", files: ["late"] });
  release(); await closing;
  assert.equal(unsubscribed, true);
  assert.deepEqual(calls, ["fuzzyFileSearch/sessionStart", "fuzzyFileSearch/sessionStop"]);
  assert.deepEqual(results, []);
});

test("only the current query and owned session can publish file results", async () => {
  let receive!: (params: Record<string, any>) => void;
  const queries: string[] = [];
  const results: unknown[] = [];
  const session = new FileSearchSession({
    observe: async (_method, callback) => { receive = callback; return () => {}; },
    call: async (method, params) => { if (method.endsWith("Update")) queries.push(params.query); },
  }, "search-b", ["/workspace"], (files) => results.push(files), (error) => assert.fail(String(error)));
  session.update("old"); session.update("new"); await tick();
  receive({ sessionId: "other", query: "new", files: ["wrong"] });
  receive({ sessionId: "search-b", query: "old", files: ["stale"] });
  receive({ sessionId: "search-b", query: "new", files: ["current"] });
  assert.deepEqual(queries, ["new"]);
  assert.deepEqual(results, [["current"]]);
  await session.close();
});

test("native file result paths preserve absolute paths and join relative paths to their root", () => {
  const match = { file_name: "界面.tsx", match_type: "file" as const, root: "/work/", path: "src/界面.tsx", score: 1 };
  assert.equal(fileMatchPath(match), "/work/src/界面.tsx");
  assert.equal(fileMatchPath({ ...match, path: "/other/file.tsx" }), "/other/file.tsx");
});
