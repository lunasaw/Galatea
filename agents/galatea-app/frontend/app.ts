import { App, applyDocumentTheme } from "@modelcontextprotocol/ext-apps";
type Obj = Record<string, any>;
const $ = (s: string) => document.querySelector(s) as HTMLElement;
const app = new App({ name: "Galatea 任务进度", version: "0.1.0" }, {});
const flow = elementFlow();
function elementFlow() {
  const area = document.createElement("div");
  area.id = "conversation-flow";
  const conversation = document.querySelector(".conversation")!;
  for (const child of Array.from(conversation.children)) {
    if (child.id !== "composer") area.append(child);
  }
  conversation.prepend(area);
  return area;
}
let connected = false,
  view: Obj | null = null,
  details: Obj | null = null,
  taskId: string | undefined,
  stageId: string | undefined,
  tab = "artifacts",
  fileId: string | undefined,
  manual = false,
  generation = 0,
  inflight = false,
  timer: ReturnType<typeof setTimeout> | undefined,
  disposed = false,
  writing = false;
const statuses: Obj = {
  draft: "草稿",
  planning: "正在规划",
  running: "执行中",
  pending: "待执行",
  ready: "已就绪",
  waiting_input: "等待你的决定",
  waiting_approval: "等待授权",
  waiting_external: "外部执行中",
  verifying: "正在核验",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  cancelling: "正在确认停止",
  paused: "已暂停",
  blocked: "等待对账",
  skipped: "已跳过",
  superseded: "已替代",
  succeeded: "成功",
  accepted: "已受理",
  queued: "排队中",
  unknown: "状态未知",
};
const symbols: Obj = {
  completed: "✓",
  failed: "!",
  cancelled: "−",
  running: "●",
  waiting_external: "◷",
  verifying: "◷",
  waiting_input: "?",
  waiting_approval: "?",
  skipped: "−",
};
const element = (tag: string, text?: string, className?: string) => {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (className) e.className = className;
  return e;
};
function notice(text: string, error = false, background = false) {
  if (background && (writing || $("#notice").classList.contains("error"))) return;
  $("#notice").textContent = text;
  $("#notice").className = error ? "error" : view?.fixture ? "fixture" : "";
}
function err(error: any) {
  notice(error?.message || String(error), true);
}
function button(text: string, run: () => any, className = "") {
  const b = element("button", text, className) as HTMLButtonElement;
  b.onclick = () => Promise.resolve().then(run).catch(err);
  return b;
}
function commandId() {
  // randomUUID is unavailable on HTTP IP origins; getRandomValues works there
  // and retains a cryptographically random 128-bit idempotency identity.
  return "app-" + Array.from(crypto.getRandomValues(new Uint8Array(16)),
    value => value.toString(16).padStart(2, "0")).join("");
}
function formatted(value: any): string {
  return value === null || value === undefined
    ? "待平台提供"
    : typeof value === "object"
      ? JSON.stringify(value, null, 2)
      : String(value);
}
function section(title: string) {
  const e = element("section", undefined, "detail-section");
  e.append(element("h4", title));
  return e;
}
function row(label: string, value: any) {
  const e = element("div", undefined, "detail-row");
  e.append(element("span", label), element("span", formatted(value)));
  return e;
}
async function call(name: string, args: Obj = {}): Promise<Obj> {
  if (!connected) throw Error("尚未连接可信 Host");
  const result = await app.callServerTool({ name, arguments: args });
  if (result.isError)
    throw Error(
      (result.structuredContent as Obj)?.message ||
        result.content?.map((x: any) => x.text || "").join("") ||
        "操作失败",
    );
  return (result.structuredContent || {}) as Obj;
}
async function write(
  name: string,
  args: Obj,
  key: string = commandId(),
) {
  if (writing) return;
  writing = true;
  notice("正在等待 Host 确认或处理操作…");
  setDisabled();
  renderActions();
  try {
    let r: Obj;
    try {
      r = await call(name, { ...args, command_id: key });
    } catch (error) {
      try {
        r = await call("get_command_receipt", { command_id: key });
      } catch {
        throw error;
      }
    }
    await refresh();
    return r;
  } finally {
    writing = false;
    setDisabled();
    renderActions();
  }
}
function setDisabled() {
  for (const id of ["stop"]) {
    const b = $("#" + id) as HTMLButtonElement;
    b.disabled = writing || !connected || !!view?.read_only;
  }
}
function selected() {
  return view?.task_snapshot?.stages.find((s: Obj) => s.id === stageId);
}
async function chooseStage(id: string) {
  stageId = id;
  manual = true;
  tab = "artifacts";
  fileId = undefined;
  $("#work").classList.remove("details-hidden");
  await loadDetails();
  render();
  void context();
}
async function context() {
  if (!view?.task_snapshot) return;
  await app.updateModelContext({
    structuredContent: {
      task_id: taskId,
      selected_stage_id: stageId,
      selected_file_change_id: fileId,
      plan_revision: view.task_snapshot.task.plan_revision,
    },
  });
}
async function loadDetails() {
  if (!taskId || !stageId) return;
  const own = generation,
    id = stageId;
  const next = await call("get_stage_details", {
    task_id: taskId,
    stage_id: stageId,
  });
  if (own === generation && stageId === id) {
    details = next;
    renderDetails();
  }
}
async function refresh() {
  if (inflight || disposed) return;
  inflight = true;
  const own = generation;
  try {
    const next = await call("get_workbench", taskId ? { task_id: taskId } : {});
    if (own !== generation) return;
    view = next;
    taskId = next.task_snapshot?.task.id;
    const stages = next.task_snapshot?.stages || [];
    if (!manual || !stages.some((s: Obj) => s.id === stageId))
      stageId = (
        stages.find((s: Obj) =>
          [
            "running",
            "verifying",
            "waiting_external",
            "waiting_input",
            "waiting_approval",
          ].includes(s.status),
        ) ||
        stages.find((s: Obj) => s.status === "ready") ||
        stages.at(-1)
      )?.id;
    render();
    await loadDetails();
    notice(
      next.fixture
        ? "模拟数据 · 所有执行操作均已禁用"
        : next.read_only
          ? "真实平台证据 · 只读复现展示"
          : next.source_cursors?.host?.coverage === "incomplete"
            ? "事件同步存在缺口 · 当前展示最后已确认事实"
            : "已连接 · 状态来自 Task Service 与原始执行证据",
      false,
      true,
    );
  } catch (error) {
    err(error);
  } finally {
    inflight = false;
    if (own !== generation && !disposed) void refresh();
  }
}
function schedule() {
  clearTimeout(timer);
  if (disposed) return;
  const terminal = ["completed", "cancelled", "failed"].includes(
    view?.task_snapshot?.task.status,
  );
  timer = setTimeout(
    async () => {
      await refresh();
      schedule();
    },
    document.hidden
      ? 30000
      : terminal
        ? 30000
        : view?.task_snapshot?.task.status === "waiting_external"
          ? 7000
          : 2500,
  );
}
function render() {
  const previousScroll = flow.scrollTop;
  const atBottom = flow.scrollHeight - flow.scrollTop - flow.clientHeight < 60;
  const s = view?.task_snapshot;
  $("#empty").hidden = !!s;
  $("#work").hidden = !s;
  $("#project-name").textContent = "任务进度与结果";
  $("#path").textContent =
    view?.project?.workspace_display_path ||
    "告诉 Agent 数据在哪里，以及希望完成什么";
  if (!s) {
    setDisabled();
    return;
  }
  $("#task-goal").textContent = s.task.goal;
  const sources = $("#sources");
  sources.replaceChildren();
  for (const input of view?.inputs || [])
    sources.append(element("code", input.path || input.id, "chip"));
  sources.append(element("span", "计划 v" + s.task.plan_revision, "chip"));
  $("#task-status").textContent = statuses[s.task.status] || s.task.status;
  const stages = $("#stages");
  stages.replaceChildren();
  s.stages
    .filter((st: Obj) => st.status !== "superseded")
    .forEach((st: Obj, i: number) => {
      const b = button(
        "",
        () => chooseStage(st.id),
        `stage ${st.status} ${st.id === stageId ? "selected" : ""} ${["running", "waiting_external", "verifying"].includes(st.status) ? "current" : ""}`,
      );
      b.append(
        element("span", symbols[st.status] || String(i + 1), "state-icon"),
        element("span", st.title),
      );
      b.title = statuses[st.status] || st.status;
      stages.append(b);
    });
  const process = $("#process");
  process.replaceChildren();
  for (const st of s.stages) {
    const card = element("article", undefined, "process-card");
    const head = button("", () => chooseStage(st.id), "card-heading");
    head.append(
      element("span", symbols[st.status] || "○"),
      element("strong", st.title),
      element("span", statuses[st.status] || st.status, "status-badge"),
    );
    card.append(head);
    const content = element("div", undefined, "card-content");
    for (const r of st.acceptance) {
      content.append(
        element(
          "div",
          `${r.state === "passed" ? "✓" : r.state === "failed" ? "!" : "○"} ${r.id} · ${r.state === "passed" ? "通过" : r.state === "failed" ? "未通过" : "待核验"}`,
          "check " + r.state,
        ),
      );
    }
    if (st.reason) content.append(element("div", st.reason));
    if (st.result && Object.keys(st.result).length) {
      const summary = Object.entries(st.result)
        .filter(
          ([k, v]) =>
            typeof v !== "object" &&
            !["fixture", "source_snapshot_baseline"].includes(k),
        )
        .slice(0, 3)
        .map(([k, v]) => k + "：" + v)
        .join(" · ");
      content.append(element("div", summary));
    }
    card.append(content);
    process.append(card);
  }
  renderActions();
  renderDetails();
  const training = s.external_operations.some(
    (o: Obj) => !["succeeded", "failed", "cancelled"].includes(o.status),
  );
  $("#stop").textContent = training ? "停止本次训练" : "停止 Agent";
  $("#stop").hidden =
    !s.allowed_actions.includes("cancel") &&
    !s.allowed_actions.includes("pause");
  setDisabled();
  flow.scrollTop = atBottom ? flow.scrollHeight : previousScroll;
  $("#new-records").hidden = atBottom;
}
function renderActions() {
  const target = $("#actions");
  target.replaceChildren();
  const s = view?.task_snapshot;
  if (!s) return;
  const proposals = view?.proposals || [];
  for (const p of proposals) {
    if (proposals.some((other: Obj) =>
      other.stage_id === p.stage_id && other.revision > p.revision)) continue;
    const request = (view?.pending_requests || [])
      .filter((r: Obj) => r.proposal_id === p.proposal_id)
      .sort((a: Obj, b: Obj) =>
        Number(!!a.parent_request_id) - Number(!!b.parent_request_id) ||
        a.created_at.localeCompare(b.created_at))
      .at(-1);
    if (!request || request.status === "superseded") continue;
    const card = element("section", undefined, "proposal");
    card.append(
      element("h3", "确认本次训练方案"),
      element(
        "div",
        `方案 v${p.revision} · ${p.immutable_inputs.role} · ${request.status === "deferred" ? "已延后" : statuses[s.task.status] || request.status}`,
        "version",
      ),
    );
    const budget = element("dl", undefined, "budget");
    const values: Obj = {
      训练目标: p.objective,
      算力: p.resources,
      内存: p.resources.memory || {
        hard_limit_bytes: p.hard_limits.memory_bytes,
      },
      次数: `${p.hard_limits.attempts} 个 Attempt · 自动重试 ${p.hard_limits.retries} 次`,
      "时长 / 费用": p.estimate,
      执行预算: `${p.hard_limits.wall_seconds} 秒 · ${
        p.hard_limits.memory_bytes === null
          ? "未设置作业内存硬上限（仅调度预留）"
          : `内存硬上限 ${p.hard_limits.memory_bytes} bytes`
      }`,
      范围: p.immutable_inputs,
      有效期: p.expires_at,
    };
    for (const [k, v] of Object.entries(values))
      budget.append(element("dt", k), element("dd", formatted(v)));
    card.append(budget);
    const readiness = element("div", undefined, "detail-section");
    readiness.append(element("h4", "就绪条件"));
    for (const check of p.readiness || [])
      readiness.append(
        element(
          "div",
          `${check.status === "passed" ? "✓" : "○"} ${check.label || check.id} · ${check.reason || check.status}`,
          "check " + check.status,
        ),
      );
    card.append(readiness);
    const buttons = element("div", undefined, "buttons");
    const authorization = view?.training_decisions?.[p.proposal_id];
    if (authorization && !authorization.authorized) {
      const authorize = button(
        "授权本次方案",
        () => write("submit_input", {
          task_id: taskId,
          request_id: request.request_id,
          expected_version: s.task.version,
          values: { proposal_digest: p.digest, decision: "authorize" },
        }),
        "primary",
      );
      authorize.disabled = !!view?.read_only || !!view?.fixture ||
        !authorization.can_authorize || writing;
      authorize.title = authorization.reason;
      buttons.append(authorize);
    }
    const confirm = button(
      "确认本次执行并训练",
      () =>
        write("submit_input", {
          task_id: taskId,
          request_id: request.request_id,
          expected_version: s.task.version,
          values: { proposal_digest: p.digest, decision: "confirm" },
        }),
      "primary",
    );
    confirm.disabled =
      !!view?.read_only ||
      !!view?.fixture ||
      (authorization ? !authorization.can_confirm : s.task.status === "waiting_approval") ||
      request.status === "accepted" ||
      writing;
    confirm.title = authorization?.confirm_reason || authorization?.reason || "";
    buttons.append(
      confirm,
      button("调整方案", () => {
        notice("请在当前聊天输入调整要求，Agent 会更新方案。");
      }),
      button("暂不训练", () =>
        write("submit_input", {
          task_id: taskId,
          request_id: request.request_id,
          expected_version: s.task.version,
          values: { proposal_digest: p.digest, decision: "defer" },
        }),
      ),
    );
    if (view?.read_only || view?.fixture)
      (buttons.lastChild as HTMLButtonElement).disabled = true;
    card.append(buttons);
    if (request.status === "deferred")
      card.append(
        element("p", "尚未提交训练。方案、代码和处理记录已保留。"),
        button("重新查看方案", () => {
          card.scrollIntoView({ block: "center" });
        }),
      );
    if (authorization)
      card.append(element("p", authorization.reason, "authorization-status"));
    else if (s.task.status === "waiting_approval")
      card.append(
        element("p", "缺少匹配的治理授权。此工作台不能创建或扩展训练预算。"),
      );
    target.append(card);
  }
  if (
    [
      "completed",
      "paused",
      "cancelled",
      "blocked",
      "failed",
      "cancelling",
    ].includes(s.task.status)
  ) {
    const result = element("section", undefined, "result-card");
    result.append(
      element("strong", statuses[s.task.status]),
      element(
        "p",
        view?.final_summary || s.task.status === "completed"
          ? view?.final_summary || "交付产物与证据已关联。"
          : s.task.status === "cancelling"
            ? "停止请求已记录，正在确认关联作业实际停止。"
            : "已有记录与产物已保留。查看详情了解后续条件。",
      ),
      button("查看验收证据", () => {
        stageId = s.stages.at(-1)?.id;
        tab = "artifacts";
        $("#work").classList.remove("details-hidden");
        return loadDetails();
      }),
    );
    target.append(result);
  }
}
function renderDetails() {
  const body = $("#detail-body");
  body.replaceChildren();
  const st = selected();
  $("#detail-stage").textContent = st
    ? `${st.title} · ${st.current_attempt ? "Attempt " + st.current_attempt.number : "尚未开始"}`
    : "选择一个阶段查看详情";
  document
    .querySelectorAll("[data-tab]")
    .forEach((e) =>
      e.setAttribute(
        "aria-selected",
        String((e as HTMLElement).dataset.tab === tab),
      ),
    );
  if (!st) {
    body.append(element("p", "Agent 正在规划任务，阶段和结果会在这里自动更新。", "empty"));
    return;
  }
  if (!details || details.stage?.id !== st.id) {
    body.append(element("p", "正在读取该阶段的真实来源…", "empty"));
    return;
  }
  if (tab === "binding") {
    const dl = element("dl", undefined, "binding-list");
    for (const [k, v] of Object.entries({
      项目: view?.project?.name,
      工作目录: view?.project?.workspace_display_path,
      数据来源: (view?.inputs || []).map((i: Obj) => i.path).join("\n"),
      参考项目: view?.reference_project || "未指定",
      "Agent 会话": view?.binding?.session_id,
      绑定状态: view?.binding?.activation_state,
      训练授权: view?.task_snapshot.task.constraints.allow_training
        ? "以当前方案与治理服务核验为准"
        : "不涉及训练",
    })) {
      dl.append(element("dt", k), element("dd", formatted(v)));
    }
    body.append(dl);
    const advanced = element("details");
    advanced.append(
      element("summary", "追踪标识"),
      element(
        "pre",
        formatted({
          ...view?.binding,
          task_id: taskId,
          plan_revision: view?.task_snapshot.task.plan_revision,
        }),
      ),
    );
    body.append(advanced);
    return;
  }
  if (tab === "logs") {
    if (!details.logs.length)
      body.append(
        element(
          "p",
          "此阶段暂无已关联的操作记录。未归属事件不会猜测归属。",
          "empty",
        ),
      );
    for (const log of details.logs) {
      const e = element("div", undefined, "log");
      e.append(
        element("small", new Date(log.occurred_at).toLocaleString()),
        element("div", log.type),
        element("pre", formatted(log.data)),
      );
      body.append(e);
    }
    body.append(element("p", details.raw_logs_reason, "empty"));
    if (details.has_more)
      body.append(
        button("加载更多日志", async () => {
          const next = await call("get_stage_details", {
            task_id: taskId,
            stage_id: stageId,
            cursor: details!.next_seq,
          });
          details = { ...next, logs: [...details!.logs, ...next.logs] };
          renderDetails();
        }),
      );
    return;
  }
  const skills = details.spec?.execution?.skill_refs || [];
  if (skills.length) {
    const sec = section("执行 Skill");
    skills.forEach((s: string) => sec.append(element("span", s, "chip")));
    sec.append(element("small", " 计划引用；加载结果见真实日志"));
    body.append(sec);
  }
  if (st.result && Object.keys(st.result).length) {
    const sec = section("阶段结果");
    for (const [k, v] of Object.entries(st.result)) {
      if (typeof v !== "object") sec.append(row(k, v));
      else {
        const extra = element("details");
        extra.append(element("summary", k), element("pre", formatted(v)));
        sec.append(extra);
      }
    }
    body.append(sec);
  }
  if (details.file_changes.length) {
    const sec = section("项目文件与差异");
    for (const f of details.file_changes) {
      const b = button(
        `${f.operation === "add" ? "+" : f.operation === "delete" ? "−" : "M"} ${f.relative_path}`,
        () => showFile(f.change_id),
        "file" + (fileId === f.change_id ? " selected" : ""),
      );
      sec.append(b);
    }
    body.append(sec);
    const diff = element("div");
    diff.id = "file-diff";
    body.append(diff);
    if (fileId) void showFile(fileId);
  }
  const artifacts = section("产物与报告");
  if (!details.artifacts.length)
    artifacts.append(element("p", "尚未生成已验证产物。", "empty"));
  for (const a of details.artifacts)
    artifacts.append(
      button(a.display_name + " ↗", () => showArtifact(a.id), "artifact"),
    );
  body.append(artifacts);
  const preview = element("div");
  preview.id = "artifact-preview";
  body.append(preview);
  if (details.operations.length) {
    const sec = section("训练执行");
    for (const o of details.operations) {
      for (const [k, v] of Object.entries({
        Operation: o.id,
        "Ray Job": o.job_id,
        "MLflow Run": o.run_id,
        状态: statuses[o.status],
        最后观察: o.last_observed_at,
      }))
        sec.append(row(k, v));
    }
    body.append(sec);
    const metrics = section("训练与验证指标");
    if (!details.metrics.length)
      metrics.append(element("p", "等待首个指标", "empty"));
    for (const m of details.metrics) {
      const e = element("div", undefined, "metric");
      e.append(
        element("span", m.name),
        element("code", Number(m.value).toPrecision(5)),
      );
      e.title = `${m.split} · step ${m.step} · ${m.observed_at || ""}`;
      metrics.append(e);
    }
    body.append(metrics);
  }
  const evidence = section("核验与证据");
  for (const e of details.evidence)
    evidence.append(
      element(
        "div",
        `${e.verification === "verified" ? "✓" : "!"} ${e.summary}`,
        "check " + (e.verification === "verified" ? "passed" : "failed"),
      ),
      element("small", e.source + " · " + e.observed_at),
    );
  body.append(evidence);
}
async function showFile(id: string) {
  fileId = id;
  const own = generation,
    stage = stageId;
  const data = await call("get_file_change", {
    task_id: taskId,
    change_id: id,
  });
  if (
    own !== generation ||
    stage !== stageId ||
    fileId !== id ||
    !$("#file-diff")
  )
    return;
  document
    .querySelectorAll(".file")
    .forEach((e) =>
      e.classList.toggle(
        "selected",
        e.textContent?.includes(data.relative_path) || false,
      ),
    );
  const target = $("#file-diff");
  target.replaceChildren(
    element(
      "small",
      `基线 ${data.base_digest.slice(0, 12)} → ${data.current_digest.slice(0, 12)}`,
    ),
  );
  const pre = element("pre", undefined, "diff");
  for (const line of data.hunks)
    pre.append(
      element(
        "span",
        line,
        "diff-line " +
          (line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : ""),
      ),
    );
  target.append(pre);
  if (data.has_more)
    target.append(
      button("更多差异", async () => {
        const next = await call("get_file_change", {
          task_id: taskId,
          change_id: id,
          cursor: data.next_cursor,
        });
        for (const line of next.hunks)
          pre.append(element("span", line, "diff-line"));
      }),
    );
  void context();
}
async function showArtifact(id: string) {
  const own = generation,
    stage = stageId;
  const data = await call("get_artifact", {
    task_id: taskId,
    artifact_id: id,
    view: "preview",
  });
  if (own !== generation || stage !== stageId) return;
  const target = $("#artifact-preview");
  target.replaceChildren(
    element(
      "small",
      `受控预览 · SHA-256 ${data.sha256.slice(0, 12)}${data.truncated ? " · 已截断" : ""}`,
    ),
    element("pre", data.content || "二进制产物仅展示身份与核验结果。"),
  );
}
$("#stop").onclick = () => {
  const s = view?.task_snapshot;
  if (!s) return;
  const training = s.external_operations.some(
    (o: Obj) => !["succeeded", "failed", "cancelled"].includes(o.status),
  );
  void write("request_control", {
    task_id: taskId,
    action: training ? "cancel" : "interrupt_agent",
    expected_version: s.task.version,
    reason: training ? "停止本次训练" : "停止 Agent",
  }).catch(err);
};
$("#toggle-details").onclick = () => {
  const hidden = $("#work").classList.toggle("details-hidden");
  $("#toggle-details").setAttribute("aria-expanded", String(!hidden));
};
$("#follow").onclick = () => {
  manual = false;
  void refresh();
};
$("#scroll-latest").onclick = () => {
  flow.scrollTop = flow.scrollHeight;
  $("#new-records").hidden = true;
};
document.querySelectorAll("[data-tab]").forEach((e) =>
  e.addEventListener("click", () => {
    tab = (e as HTMLElement).dataset.tab!;
    renderDetails();
  }),
);
$("#path").onclick = () =>
  navigator.clipboard?.writeText($("#path").textContent || "").catch(() => {});
$("#fullscreen").onclick = () =>
  app.requestDisplayMode({ mode: "fullscreen" }).catch(err);
app.ontoolinput = ({ arguments: args }) => {
  if (typeof args?.task_id === "string") {
    if (taskId === args.task_id) return;
    taskId = args.task_id;
    stageId = undefined;
    details = null;
    manual = false;
    generation++;
    if (connected) void refresh();
  }
};
app.ontoolresult = () => {
  if (connected) void refresh();
};
app.onhostcontextchanged = (context) => {
  if (context.theme) applyDocumentTheme(context.theme);
};
app.onteardown = async () => {
  disposed = true;
  clearTimeout(timer);
  return {};
};
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) void refresh();
  schedule();
});
app
  .connect()
  .then(async () => {
    connected = true;
    const ctx = app.getHostContext();
    if (ctx?.theme) applyDocumentTheme(ctx.theme);
    await refresh();
    schedule();
  })
  .catch(err);
