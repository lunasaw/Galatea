import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { ConsoleStore } from "./store.ts";
import { parseProjectLayout, moveProject } from "./project-state.ts";
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
test("startup loads model defaults while projects are pending and reuses the initial catalog", async () => {
  const s = make();
  let releaseProjects!: () => void;
  const pendingProjects = new Promise<void>(resolve => { releaseProjects = resolve; });
  const calls: string[] = [];
  globalThis.fetch = async (path, init) => {
    const url = String(path);
    if (url === "/api/console/status") return json({});
    if (url === "/api/sessions" || url === "/api/native/requests") return json([]);
    if (url === "/api/capabilities") return json({});
    if (url === "/api/native/protocol") return json({ methods: ["project/list"], cwd: "/startup" });
    if (url === "/api/sidebar-groups") return json({ sections: [], revision: 0 });
    const { method } = JSON.parse(init!.body as string);
    calls.push(method);
    if (method === "project/list") await pendingProjects;
    if (method === "model/list") return json({ data: [{ model: "fixture" }] });
    if (method === "config/read") return json({ config: { model: "fixture" } });
    return json({ data: [], nextCursor: null });
  };
  const initialized = s.init();
  try {
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(s.state.models[0]?.model, "fixture");
    assert.equal(s.state.preferences.model, "fixture");
  } finally {
    releaseProjects();
    await initialized;
  }
  assert.equal(calls.filter(method => method === "project/list").length, 1);
  assert.equal(calls.filter(method => method === "threadSection/list").length, 1);
  assert.equal(s.state.error, "");
});
test("sidebar reads native order in each section including unsectioned threads", async () => {
  const s = make();
  s.patch({ sessions: [
    { id: "a", thread_id: "native-a", status: "ready" },
    { id: "b", thread_id: "native-b", status: "ready" },
    { id: "c", thread_id: "native-c", status: "ready" },
  ] });
  const sectionRequests: unknown[] = [];
  globalThis.fetch = async (path, init) => {
    if (String(path) === "/api/sidebar-groups") return json({ sections: [], revision: 0 });
    const { method, params } = JSON.parse(init!.body as string);
    if (method === "project/list") return json({ data: [], nextCursor: null });
    if (method === "threadSection/list") return json({ data: [{ id: "group", name: "Group" }], nextCursor: null });
    assert.equal(method, "thread/list");
    assert.ok(Object.hasOwn(params, "sectionId"));
    assert.equal(params.sortKey, params.sectionId === null ? "updated_at" : "section_position");
    sectionRequests.push(params.sectionId);
    if (params.sectionId === null) return json({ data: [{ id: "native-a" }], nextCursor: null });
    return json({ data: [{ id: params.cursor ? "native-b" : "native-c", section: { id: "group" } }],
      nextCursor: params.cursor ? null : "next" });
  };
  await s.refreshProjects();
  assert.deepEqual(sectionRequests, [null, "group", "group"]);
  assert.deepEqual(s.state.projectLayout.threadOrder, ["a", "c", "b"]);
  assert.deepEqual(s.state.sessions.map((session) => session.section_id), [undefined, "group", "group"]);
});
test("switching permission profiles removes obsolete sandbox settings from the visible session", async () => {
  const s = make();
  s.patch({ sessions: [{ id: "a", thread_id: "native-a", status: "ready", settings: {
    sandbox: "danger-full-access", sandboxPolicy: { type: "dangerFullAccess" }, model: "fixture",
  } }] });
  globalThis.fetch = async () => json({});
  assert.equal(await s.updateSessionSettings("a", { permissions: "locked" }), true);
  assert.deepEqual(s.state.sessions[0].settings, { permissions: "locked", model: "fixture" });
  assert.equal(await s.updateSessionSettings("a", { sandboxPolicy: { type: "readOnly" } }), true);
  assert.deepEqual(s.state.sessions[0].settings, { sandboxPolicy: { type: "readOnly" }, model: "fixture" });
});
test("goal and plan inputs are exclusive only after native acceptance and belong to their chat", async () => {
  const s = make();
  s.patch({ active: "a", sessions: [{ id: "a", thread_id: "native-a", status: "ready", settings: {
    collaborationMode: { mode: "plan", settings: { model: "fixture", reasoning_effort: "high" } },
  } }] });
  globalThis.fetch = async () => new Response('{"error":"rejected"}', { status: 400 });
  assert.equal(await s.chooseGoalMode(true), false);
  assert.equal(s.goalInputMode, false);
  assert.equal(s.composerSettings.mode, "plan");
  globalThis.fetch = async () => json({});
  assert.equal(await s.chooseGoalMode(true), true);
  assert.equal(s.composerSettings.mode, "default");
  s.patch({ active: "b" });
  assert.equal(s.goalInputMode, false);
  s.patch({ active: "a" });
  assert.equal(s.goalInputMode, true);
  assert.equal(await s.updateComposerSettings({ mode: "plan" }), true);
  assert.equal(s.goalInputMode, false);
});

test("goal sends use only the native goal method and retry an uncertain response with the same identity", async () => {
  const s = make();
  s.patch({ active: "a", sessions: [{ id: "a", thread_id: "native-a", status: "ready" }] });
  await s.chooseGoalMode(true);
  const calls: RequestInit[] = [];
  globalThis.fetch = async (path, init) => {
    if (String(path) === "/api/native/call") {
      calls.push(init!);
      if (calls.length === 1) throw new Error("lost response");
      return json({ goal: { objective: "目标", status: "active" } });
    }
    assert.equal(String(path), "/api/sessions");
    return json(s.state.sessions);
  };
  assert.equal(await s.sendGoal("目标"), false);
  assert.equal(s.goalInputMode, true);
  assert.equal(await s.sendGoal("目标"), true);
  assert.deepEqual(calls[0], calls[1]);
  assert.deepEqual(JSON.parse(calls[0].body as string), { method: "thread/goal/set", params: { threadId: "native-a", objective: "目标" } });
  assert.equal(s.goalInputMode, false);
  assert.equal(s.state.goalRevision, 1);
});

test("a new goal retries in the already created chat without creating a second one", async () => {
  const s = make(); let creations = 0;
  s.create = async (activate) => {
    assert.equal(activate, false); creations++;
    s.patch({ sessions: [{ id: "new-goal", thread_id: "native-goal", status: "ready" }] });
    return "new-goal";
  };
  s.select = async (id) => { s.patch({ active: id }); };
  s.refreshSessions = async () => {};
  await s.chooseGoalMode(true);
  globalThis.fetch = async () => { throw new Error("unavailable"); };
  assert.equal(await s.sendGoal("原目标"), false);
  assert.equal(s.state.active, null);
  globalThis.fetch = async () => json({});
  assert.equal(await s.sendGoal("修订目标"), true);
  assert.equal(creations, 1);
  assert.equal(s.state.active, "new-goal");
});
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
test("slash commands reject missing capabilities and running mutations before model submission", async () => {
  const s = make();
  let requests = 0;
  globalThis.fetch = async () => { requests++; throw new Error("unexpected request"); };
  await s.send("/aside");
  assert.match(s.state.error, /临时侧边聊天/);
  await s.send("/share");
  assert.match(s.state.error, /快照分享/);
  await s.send("/archive");
  assert.match(s.state.error, /先开始聊天/);
  s.patch({ active: "a", sessions: [{ id: "a", thread_id: "native-a", status: "running", active_turn_id: "turn-a" }] });
  for (const command of ["/init", "/compact", "/fork", "/archive"]) {
    await s.send(command);
    assert.match(s.state.error, /先停止当前回复/);
  }
  assert.equal(requests, 0);
});
test("init sends a repository guide task through the normal native message path", async () => {
  const s = make();
  s.patch({ active: "a", sessions: [{ id: "a", thread_id: "native-a", status: "ready" }] });
  const messages: { text: string }[] = [];
  globalThis.fetch = async (path, init) => {
    if (String(path).endsWith("/messages")) { messages.push(JSON.parse(init!.body as string)); return json({ status: "accepted" }); }
    return json([{ id: "a", thread_id: "native-a", status: "ready" }]);
  };
  await s.send("/init");
  assert.equal(messages.length, 1);
  assert.match(messages[0].text, /先检查.*AGENTS\.md.*若已存在，不覆盖、不修改/);
  assert.ok(!messages[0].text.startsWith("/"));
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
test("history retains all item types and loaded pages at the cursor watermark", async () => {
  const s = make();
  s.patch({
    active: "a",
    snapshot: { ...snapshot("latest"), next_page: "opaque" },
  });
  const urls: string[] = [];
  globalThis.fetch = async (path) => {
    urls.push(String(path));
    return json({
      ...snapshot("old"),
      steps: [
        { id: "tool", type: "commandExecution", first_seq: "2", last_seq: "3" },
      ],
      turns: [{ id: "turn", status: "completed" }],
    });
  };
  await s.older();
  assert.ok(urls[0].includes("/snapshot?limit=200&view=all&page=opaque"));
  assert.equal(s.state.history, true);
  assert.deepEqual(
    s.state.snapshot?.messages?.map((m) => m.id),
    ["old", "latest"],
  );
  assert.equal(s.state.snapshot?.steps?.[0].id, "tool");
  assert.equal(s.state.snapshot?.turns?.[0].status, "completed");
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

test("history refresh rebuilds loaded pages at one new watermark and retains input", async () => {
  const s = make();
  s.patch({
    active: "a",
    snapshot: { ...snapshot("latest"), next_page: "old-page" },
  });
  let refreshing = false;
  let finished!: () => void;
  const refreshed = new Promise<void>((resolve) => {
    finished = resolve;
  });
  globalThis.fetch = async (path) => {
    const url = String(path);
    if (url.endsWith("/sessions")) return json([{ id: "a", status: "ready" }]);
    if (url.includes("page=old-page"))
      return json({ ...snapshot("old"), next_page: null });
    if (url.endsWith("/snapshot")) {
      refreshing = true;
      return json({
        ...snapshot("newest"),
        watermark: "e:20",
        next_page: "new-page",
        steps: [{ id: "new-tool" }],
      });
    }
    if (url.includes("page=new-page")) {
      queueMicrotask(finished);
      return json({
        ...snapshot("old"),
        watermark: "e:20",
        steps: [{ id: "old-tool" }],
      });
    }
    throw Error(url);
  };
  await s.older();
  await refreshed;
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(refreshing, true);
  assert.equal(s.state.snapshot?.watermark, "e:20");
  assert.deepEqual(
    s.state.snapshot?.messages?.map((m) => m.id),
    ["old", "newest"],
  );
  assert.deepEqual(
    s.state.snapshot?.steps?.map((m) => m.id),
    ["old-tool", "new-tool"],
  );
});

test("permission selection updates the native thread, preserves state on failure, and never affects another chat", async () => {
  const s = make();
  s.patch({
    active: "a",
    sessions: [
      {
        id: "a",
        thread_id: "native-a",
        status: "ready",
        settings: { sandbox: "workspace-write", approvalPolicy: "on-request" },
      },
      { id: "b", thread_id: "native-b", status: "ready" },
    ],
  });
  let request: any;
  globalThis.fetch = async (_, init) => {
    request = JSON.parse(init!.body as string);
    return json({});
  };
  assert.equal(await s.choosePermission("auto"), true);
  assert.equal(request.method, "thread/settings/update");
  assert.equal(request.params.threadId, "native-a");
  assert.equal(request.params.approvalsReviewer, "auto_review");
  assert.equal(request.params.sandboxPolicy.type, "workspaceWrite");
  assert.equal(s.state.sessions[1].settings, undefined);
  globalThis.fetch = async () =>
    new Response('{"error":"policy-rejected"}', { status: 400 });
  assert.equal(await s.choosePermission("full"), false);
  assert.equal(s.permissionConfiguration.approvalsReviewer, "auto_review");
  assert.equal(s.permissionConfiguration.sandboxPolicy.type, "workspaceWrite");
  assert.equal(s.state.settingsBusy, false);
});

test("project changes persist only after native acceptance and cannot move a running session", async () => {
  const s = make();
  s.patch({
    active: "a",
    sessions: [
      { id: "a", thread_id: "native-a", cwd: "/old", status: "ready" },
    ],
  });
  globalThis.fetch = async () =>
    new Response('{"error":"missing-directory"}', { status: 400 });
  assert.equal(await s.chooseProject(s.addProject("/missing").id), false);
  assert.equal(s.cwd, "/old");
  assert.equal(memory.has("codex-agent.project"), false);
  globalThis.fetch = async () => json({});
  assert.equal(await s.chooseProject(s.addProject("/new").id), true);
  assert.equal(s.cwd, "/new");
  assert.equal(memory.get("codex-agent.project"), "/new");
  s.patch({
    sessions: [
      { ...s.state.sessions[0], active_turn_id: "turn", status: "running" },
    ],
  });
  globalThis.fetch = async () => {
    throw new Error("must not call while running");
  };
  assert.equal(await s.chooseProject(s.addProject("/another").id), false);
  assert.equal(s.cwd, "/new");
});

test("new chat creation sends the selected project and permission before the first turn", async () => {
  const s = make();
  s.patch({
    draftCwd: "/project",
    preferences: { model: "", effort: "medium", mode: "default" },
  });
  await s.choosePermission("full");
  let options: any;
  globalThis.fetch = async (path, init) => {
    if (String(path) === "/api/sessions" && init?.method === "POST") {
      options = JSON.parse(init.body as string).options;
      return json({
        id: "a",
        thread_id: "native-a",
        status: "ready",
        cwd: options.cwd,
        settings: options,
      });
    }
    return json([]);
  };
  s.refreshSessions = async () => {};
  s.select = async () => {};
  assert.equal(await s.create(), "a");
  assert.equal(options.cwd, "/project");
  assert.equal(options.sandbox, "danger-full-access");
  assert.equal(options.approvalPolicy, "never");
  assert.equal(options.approvalsReviewer, "user");
});

test("same-folder projects keep separate identities, session membership and draft selection after reload", async () => {
  const s = make();
  s.patch({
    sessions: [
      { id: "old", thread_id: "native-old", cwd: "/shared", status: "ready" },
    ],
  });
  const originalId = s.projectForSession("old")!.id;
  const a = s.addProject("/shared", "项目 A");
  const b = s.addProject("/shared", "项目 B", "work");
  assert.notEqual(a.id, b.id);
  assert.equal(s.projects.filter((p) => p.path === "/shared").length, 3);
  assert.equal(s.projectForSession("old")!.id, originalId);
  await s.startProject(b.id);
  assert.equal(s.currentProject?.name, "项目 B");
  let createdOptions: any;
  globalThis.fetch = async (path, init) => {
    assert.equal(String(path), "/api/sessions");
    createdOptions = JSON.parse(init!.body as string).options;
    return json({
      id: "new",
      thread_id: "native-new",
      cwd: "/shared",
      status: "ready",
    });
  };
  s.refreshSessions = async () => {
    s.patch({
      sessions: [
        ...s.state.sessions,
        { id: "new", thread_id: "native-new", cwd: "/shared", status: "ready" },
      ],
    });
  };
  s.select = async (id) => {
    s.patch({ active: id });
  };
  await s.create();
  assert.equal(createdOptions.cwd, "/shared");
  assert.equal(s.projectForSession("new")?.id, b.id);
  globalThis.fetch = async () => {
    throw new Error("same directory move must not call the runtime");
  };
  assert.equal(await s.moveSessionToProject("new", a.id), true);
  assert.equal(s.projectForSession("new")?.id, a.id);
  assert.equal(s.projectForSession("old")?.id, originalId);
  s.saveProjectLayout({
    projects: moveProject(s.projects, b.id, "", originalId),
    collapsed: { [b.id]: true },
  });
  const restored = make();
  restored.patch({
    sessions: s.state.sessions,
    projectLayout: parseProjectLayout(memory.get("codex-agent.projects")!),
    draftCwd: memory.get("codex-agent.project")!,
    draftProjectId: memory.get("codex-agent.project-id")!,
  });
  assert.equal(restored.currentProject?.id, b.id);
  assert.equal(restored.projectForSession("new")?.id, a.id);
  assert.equal(restored.projectForSession("old")?.id, originalId);
  assert.equal(restored.state.projectLayout.collapsed[a.id], undefined);
  assert.equal(restored.state.projectLayout.collapsed[b.id], true);
});

test("removing a shared-folder project preserves chats without rediscovery or reassignment after reload", () => {
  const s = make();
  s.patch({
    sessions: [
      { id: "a", status: "ready", cwd: "/shared" },
      { id: "b", status: "ready", cwd: "/shared" },
    ],
  });
  const old = s.projects[0];
  const second = s.addProject("/shared", "项目 B");
  s.saveProjectLayout({ threadProjects: { a: old.id, b: second.id } });
  s.removeProject(old.id);
  assert.equal(s.projectForSession("a"), undefined);
  assert.equal(s.projectForSession("b")?.id, second.id);
  assert.equal(s.state.sessions.length, 2);
  const restored = parseProjectLayout(memory.get("codex-agent.projects")!);
  s.patch({ projectLayout: restored });
  assert.deepEqual(
    s.projects.map((p) => p.id),
    [second.id],
  );
  s.removeProject(second.id);
  s.saveProjectLayout({});
  assert.deepEqual(s.projects, []);
  assert.equal(s.projectForSession("a"), undefined);
  assert.equal(s.projectForSession("b"), undefined);
  s.addProject("/shared", "重新添加");
  assert.equal(s.projects.length, 1);
  assert.equal(s.projectForSession("a"), undefined);
});
