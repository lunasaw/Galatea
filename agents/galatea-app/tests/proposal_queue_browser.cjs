// Isolated UI regression: the mock cannot sign a decision or start real training.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const { chromium } = require("../../codex-app-server/frontend/node_modules/playwright");

(async () => {
  const stages = [
    { id: "trial", title: "原参数训练", status: "completed", current_attempt: { number: 1 } },
    { id: "candidate", title: "修正候选资格并训练", status: "waiting_approval", current_attempt: null },
  ].map(s => ({ ...s, acceptance: [], artifact_refs: [] }));
  const proposal = (id, stage, revision, plan) => ({
    proposal_id: id, stage_id: stage, revision, plan_revision: plan,
    digest: "digest-" + id, objective: "目标：" + id,
    immutable_inputs: { role: "trial" }, resources: { cpu: 19, gpu: 1 },
    hard_limits: { attempts: 1, retries: 1, wall_seconds: 1800, memory_bytes: null },
    estimate: {}, readiness: [], expires_at: "2099-01-01T00:00:00Z",
  });
  const request = (id, stage, status, date = "2026-10-20T03:37:23Z") => ({
    proposal_id: id, stage_id: stage, status, created_at: date, request_id: "req-" + id,
  });
  const view = {
    task_snapshot: {
      task: { id: "fixture-task", version: 134, plan_revision: 4, status: "waiting_approval", goal: "测试方案队列" },
      stages, external_operations: [], allowed_actions: [],
    },
    // Deliberately not chronological: production objects are serialized by key.
    proposals: [
      proposal("old", "trial", 1, 3),
      proposal("current", "candidate", 2, 4),
      proposal("stale", "candidate", 1, 4),
    ],
    pending_requests: [
      request("old", "trial", "accepted", "2026-10-20T03:00:05Z"),
      request("current", "candidate", "open"),
      request("stale", "candidate", "superseded"),
    ],
    training_decisions: {
      old: { authorized: false, can_authorize: false, can_confirm: false, reason: "方案已处理" },
      current: { authorized: false, can_authorize: true, can_confirm: false, reason: "等待授权" },
    },
  };
  const mutations = [];
  const staticRoot = path.resolve(__dirname, "../src/galatea_app/static");
  const server = http.createServer(async (req, res) => {
    if (req.url === "/" || req.url === "/preview/app") {
      res.setHeader("Content-Type", "text/html");
      return res.end(fs.readFileSync(path.join(staticRoot,
        req.url === "/" ? "preview-host.html" : "workbench.html")));
    }
    if (req.url !== "/preview/tool" || req.method !== "POST") {
      res.statusCode = 404;
      return res.end();
    }
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const { name, arguments: args } = JSON.parse(Buffer.concat(chunks));
    let value;
    if (name === "get_workbench") value = view;
    else if (name === "get_stage_details") value = {
      stage: stages.find(s => s.id === args.stage_id), spec: {},
      logs: [], artifacts: [], file_changes: [], operations: [], evidence: [], metrics: [],
    };
    else if (name === "submit_input") {
      mutations.push(args);
      assert.equal(args.task_id, "fixture-task");
      assert.equal(args.request_id, "req-current");
      assert.equal(args.values.proposal_digest, "digest-current");
      assert.equal(args.expected_version, view.task_snapshot.task.version);
      view.task_snapshot.task.version++;
      const authorized = args.values.decision === "authorize";
      view.task_snapshot.task.status = stages[1].status = authorized ? "waiting_input" : "ready";
      view.training_decisions.current = {
        authorized: true, can_authorize: false, can_confirm: authorized,
        reason: authorized ? "本次方案已有授权；确认执行后才会开始训练。" : "已确认执行",
      };
      if (!authorized) view.pending_requests[1].status = "accepted";
      value = { ok: true, result: { state: authorized ? "authorized" : "accepted" } };
    } else throw Error("Unexpected mock tool: " + name);
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ structuredContent: value, content: [] }));
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_EXECUTABLE ||
      "/root/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome",
    args: ["--no-sandbox"],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    const app = page.frameLocator("iframe");
    await app.locator(".proposal h3").filter({ hasText: "修正候选资格并训练" }).waitFor();
    assert.equal(await app.locator(".proposal").count(), 1);
    assert.match(await app.locator(".proposal .version").textContent(), /计划 v4 · 方案 v2/);
    assert.equal(await app.locator("#actions").evaluate(e =>
      !!(e.compareDocumentPosition(document.querySelector("#process")) & Node.DOCUMENT_POSITION_FOLLOWING)), true);
    await app.getByRole("button", { name: "授权本次方案", exact: true }).click();
    await app.locator("#notice").filter({ hasText: "授权已保存" }).waitFor();
    const confirm = app.getByRole("button", { name: "确认本次执行并训练", exact: true });
    assert.equal(await confirm.isEnabled(), true);
    await page.reload();
    await app.getByText("本次方案已有授权；确认执行后才会开始训练。", { exact: true }).waitFor();
    assert.equal(await app.locator(".proposal").count(), 1);
    await confirm.click();
    await app.locator("#notice").filter({ hasText: "执行确认已保存" }).waitFor();
    assert.equal(await app.locator(".proposal").count(), 0);
    await app.locator(".stage").first().click();
    await app.getByText("计划 v3 · 方案 v1 · 已确认执行", { exact: true }).waitFor();
    assert.deepEqual(mutations.map(v => v.values.decision), ["authorize", "confirm"]);
    // An obsolete open request must also stay out of the current action queue.
    view.pending_requests[1].status = "open";
    stages[1].status = "waiting_approval";
    view.task_snapshot.task.status = "waiting_approval";
    view.task_snapshot.task.plan_revision = 5;
    await page.reload();
    await app.locator("#task-status").filter({ hasText: "等待授权" }).waitFor();
    assert.equal(await app.locator(".proposal").count(), 0);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true,
      checks: ["current proposal only", "stage and plan labels", "current digest dispatch",
        "authorization reload", "accepted history", "stale plan excluded"],
      real_authority_used: false, training_submitted: false }));
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exit(1); });
