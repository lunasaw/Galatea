import { writeFileSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";
import { STAGES, STOPS, allowedStage, stageContent } from "./model.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../../../..");
const SKILL = process.env.ANATOMY_SKILL || resolve(ROOT, "agents/codex-app-server/.local/codex-home-v2/skills/anatomy");
const k = await import(pathToFileURL(resolve(SKILL, "kit/iso-kit.mjs")));
const A = await import(pathToFileURL(resolve(SKILL, "kit/audit.mjs")));
const theme = process.argv.includes("--light") ? "light" : "dark";
const W = 940, H = 520;
const base = { x: -218, y: -90, w: 436, d: 180, r: 9 };
const P = k.fitProjection(k.boxCorners(base, -18, 108), W, H, { pad: 48, azimuth: 58, elevation: 32 });
// The tall gate occupies only the middle: center the actual machine, not its empty bounding-box roof.
P.origin[1] -= 42;
const R = A.recorder(P);
const at = (p) => k.iso(p, P);
const L = (d, tone = "lo", more = {}) => k.lineSvg(d, { tone, ...more });
const D = (points, tone = "mid") => k.dotsSvg(points.map(at), { size: 0.55, tone });
const splan = (x, y, w, d, r = 1) => ({ x, y, w, d, r });
const x0 = -150, travel = 300;

function slab(name, plan, z, h, { tone = "mid", moving = false, detail = "", lit = false, bevel = 0.5 } = {}) {
  const paths = k.slabOf(plan, z, h, P, 8, Math.min(bevel, h / 3));
  const shape = A.slab(plan, z, h, { name, owner: name }, 8);
  return R.put({
    name, piece: moving ? "carriage" : "fixed",
    at: [plan.x + plan.w / 2, plan.y + plan.d / 2, z + h / 2],
    move: moving ? [travel, 0, 0] : [0, 0, 0],
    shapes: [R.solid(shape)],
    svg: k.solidSvg(paths, { tone, lit }) + detail,
  });
}
function outline(plan, z, inset = 2, tone = "faint") {
  return L(k.planOutline(k.insetPlan(plan, inset), z, P), tone);
}
function screws(plan, z, inset = 4) {
  return k.corners(plan, inset).map(([x, y]) => (
    L(k.planOutline(splan(x - 1.1, y - 1.1, 2.2, 2.2, 0.6), z, P), "lo") +
    D([[x, y, z]], "hi")
  )).join("");
}
function paperDetail(plan, z, count = 3) {
  return Array.from({ length: count }, (_, i) => L(
    k.lineOnTop([plan.x + 3, plan.y + 4 + i * 4], [plan.x + plan.w - 4 - (i % 2) * 4, plan.y + 4 + i * 4], z, P),
    "lo", { free: true },
  )).join("");
}

// The shapes recorded here are the same plans and heights that are drawn.
for (const [i, [x, y]] of k.corners(base, 27).entries()) {
  slab(`stand.foot${i}`, splan(x - 9, y - 9, 18, 18, 4), -18, 4, { tone: "lo" });
  slab(`stand.mount${i}`, splan(x - 6, y - 6, 12, 12, 2), -14, 4, { tone: "mid" });
}
slab("stand.deck", base, -10, 10, {
  tone: "lo", bevel: 1.6, detail: outline(base, 0, 5, "lo"),
});
for (const [i, [x, y]] of k.corners(base, 12).entries()) {
  const p = splan(x - 2, y - 2, 4, 4, 0.8);
  slab(`deck.bolt${i}`, p, 0, 0.6, {
    detail: L(k.lineOnTop([x - 2, y], [x + 2, y], 0.6, P), "mid"),
  });
}
const label = splan(-35, 67, 68, 12, 1);
slab("deck.label", label, 0, 0.7, {
  detail: outline(label, 0.7, 1.3) + k.faceTextSvg(k.topMatrix([-1, 73], 0.7, P, "x"), "CODEX / GALATEA", { size: 3.2, tone: "mid", anchor: "middle" }),
});
const ruler = splan(-184, 45, 368, 7, 0.6);
const ticks = k.topTicks(-180, 180, 5, 5, 45, 0.6, [2, 4.4], P);
slab("deck.ruler", ruler, 0, 0.6, { detail: L(ticks.minor, "lo") + L(ticks.major, "mid") });

for (const y of [-24, 19]) {
  const rail = splan(-185, y, 370, 5, 0.6);
  slab(`rail.${y}`, rail, 0, 10, {
    detail: L(k.lineOnTop([-185, y + 2.5], [185, y + 2.5], 10, P), "faint"),
  });
  for (const x of [-193, 185]) {
    const block = splan(x, y - 3, 8, 11, 1);
    slab(`rail.end.${x}.${y}`, block, 0, 13, { detail: screws(block, 13, 2) });
    slab(`rail.lock.${x}.${y}`, splan(x + 2, y, 4, 5, 0.7), 13, 1, { tone: "hi" });
  }
}
for (const x of [-199, 193]) slab(`rail.stop.${x}`, splan(x, -29, 6, 58, 1), 0, 15, { detail: screws(splan(x, -29, 6, 58, 1), 15, 2) });

// The ticket bins sit beside the track and never intersect the moving card.
function bin(name, x, y, w, d) {
  slab(`${name}.floor`, splan(x, y, w, d, 1), 0, 3, { tone: "lo" });
  slab(`${name}.left`, splan(x, y, 2, d, 0.4), 3, 9);
  slab(`${name}.right`, splan(x + w - 2, y, 2, d, 0.4), 3, 9);
  slab(`${name}.end`, splan(x + 2, y, w - 4, 2, 0.3), 3, 9);
}
bin("input", -183, -78, 50, 36);
for (let i = 0; i < 3; i++) {
  const p = splan(-177 + i * 1.5, -72 + i, 36, 25, 0.3);
  slab(`input.ticket${i}`, p, 3 + i * 0.7, 0.7, { tone: "hi", detail: paperDetail(p, 3.7 + i * 0.7, 3) });
}
bin("ledger", -99, -80, 66, 38);
slab("ledger.lip", splan(-97, -44, 62, 2, 0.3), 3, 4);
for (let i = 0; i < 4; i++) slab(`ledger.divider${i}`, splan(-93 + i * 15, -76, 1.3, 30, 0.3), 3, 18 + i * 2, { tone: "hi" });
bin("result", 131, -78, 54, 36);

// A dual inspection head: user-confirmed proposal and matching governance.
for (const y of [-39, 30]) {
  const mount = splan(28, y, 20, 9, 1);
  slab(`gate.mount.${y}`, mount, 0, 4, { detail: screws(mount, 4, 2) });
  slab(`gate.column.${y}`, splan(34, y + 1, 8, 7, 0.6), 4, 80, {
    detail: outline(splan(34, y + 1, 8, 7, 0.6), 84, 1.2),
  });
}
const beam = splan(28, -40, 20, 80, 1.4);
slab("gate.beam", beam, 84, 9, { tone: "hi", detail: outline(beam, 93, 2) + screws(beam, 93, 4) });
for (const y of [-18, 6]) {
  const head = splan(30, y, 16, 12, 1);
  slab(`gate.reader.${y}`, head, 67, 17, {
    tone: "hi", detail: L(k.sideSeam(head, 72, P, 8), "lo"),
  });
  slab(`gate.adjust.${y}`, splan(31, y, 14, 12, 1), 93, 3, { detail: screws(splan(31, y, 14, 12, 1), 96, 3) });
}
const control = splan(81, 53, 92, 25, 3);
slab("control.base", control, 0, 8, { tone: "lo" });
slab("control.panel", k.insetPlan(control, 1), 8, 1.5, { detail: outline(k.insetPlan(control, 1), 9.5, 2) + screws(control, 9.5, 4) });
for (let i = 0; i < 3; i++) {
  const p = splan(91 + i * 25, 60, 18, 11, 1.4);
  slab(`control.seat${i}`, p, 9.5, 1, { tone: "lo" });
  slab(`control.key${i}`, k.insetPlan(p, 2), 10.5, 2.5, { tone: "hi" });
}

// The carriage rides on, rather than through, the two rail surfaces.
for (const y of [-24, 19]) slab(`carriage.shoe.${y}`, splan(x0 - 12, y, 24, 5, 0.5), 10, 3, { moving: true });
const tray = splan(x0 - 18, -28, 36, 56, 2);
slab("carriage.tray", tray, 13, 4, { moving: true, tone: "hi", detail: outline(tray, 17, 2) });
for (const y of [-26, 23]) slab(`carriage.clip.${y}`, splan(x0 - 15, y, 30, 3, 0.6), 17, 2, { moving: true });
const ticket = splan(x0 - 13, -21, 26, 42, 0.6);
slab("carriage.ticket", ticket, 17, 0.6, {
  moving: true, lit: true, tone: "hi",
  detail: paperDetail(ticket, 17.6, 7) + D([[x0 + 8, 15, 17.6]], "hi"),
});

const depthStates = Array.from({ length: 31 }, (_, i) => i / 30);
A.settle(R, P, { states: depthStates });
R.items.sort((a, b) => a.key - b.key);
const halo = `<path class="iso-halo" d="${k.haloOf(base, -18, 10, P)}"/>`;
const body = halo + `<g id="machine-parts">${R.items.map((item, index) =>
  `<g id="part-${index}" data-name="${item.name}">${item.svg}</g>`).join("")}</g>`;
const svg = k.figureSvg({
  width: W, height: H,
  label: "精密工单检验机：一张工单在双导轨滑台上依次经过输入、台账、双检验头和结果区。滑台表示原聊天请求的推进，双检验头表示训练确认与治理授权。",
  body,
});
const machineData = R.items.map((item, index) => ({
  id: `part-${index}`, key: item.key, moving: item.piece === "carriage",
  lift: k.depthOf(item.move, P),
}));
const vector = k.axisVector(P, [travel, 0, 0]);
const plate = k.plateHtml({
  fig: "FIG 01", title: "一张工单，如何成为可信结果",
  hint: "点击步骤 / ← → 切换", readout: "01 / 06 · 聊天入口",
  keys: [
    { mark: "lit", label: "工单滑台 · 请求的推进" },
    { mark: "raised", label: "双检验头 · 训练确认与治理" },
    { mark: "edge", label: "票据匣 · 持久化任务与证据" },
  ],
  caption: "这是一台讲解用的工单检验机。六站是叙事顺序，不是产品固定阶段；位置和开关都不是实时任务状态。",
  body: `<div class="iso-stage" tabindex="0" role="group" aria-label="架构演示，用左右方向键切换六个讲解步骤">${svg}</div>`,
});

const sources = [
  ["整体架构", "doc/agent-galatea.md", "Host、任务 App、两条治理路线与验收边界"],
  ["进程托管", "agents/codex-app-server/src/codex_agent/process.py", "ManagedProcess.start 启动原生 app-server；stdio JSON-RPC"],
  ["通用 Host 装配", "agents/codex-app-server/src/codex_agent/app.py", "动态 Provider、Registry、AppIntegrations"],
  ["任务工具与自动展示", "agents/galatea-app/src/galatea_app/integrations/codex_provider.py", "start_task / get_current_task 回执携带 app_view"],
  ["状态与核验", "agents/galatea-app/src/galatea_app/service.py", "_cmd_begin_stage、verify_pending、_authorized、_cmd_complete_task"],
  ["观察与接续", "agents/galatea-app/src/galatea_app/workers.py", "tick / deliver；不创建模型客户端"],
  ["当前项目契约", "train-model/ray-cats-and-dogs-codex-app/galatea.project.yaml", "executionBackend: ray；目标 val_accuracy，方向 max"],
  ["验收记录", "agents/galatea-app/docs/10-implementation-acceptance.md", "CSV 真实会话闭环；真实 smoke 导入展示与完整训练闭环分开"],
];
const sourceRows = sources.map(([label, path, note]) => {
  const source = readFileSync(resolve(ROOT, path), "utf8");
  return `<li><strong>${label}</strong><code>${path}</code><span>${note}</span><small>当前文件 ${source.split("\n").length} 行</small></li>`;
}).join("");

const content = `
<header><div class="eyebrow">系统拆解 / SOURCE-VERIFIED · 2026.10.19</div>
<div class="title-row"><h1>Codex 负责行动，<br>Galatea 让任务可追溯。</h1><button id="theme" aria-label="切换明暗主题">切换主题</button></div>
<p class="intro">你现在用的是一个通用 Codex 工作台，接上独立的 Galatea 任务与治理服务。理解它们，先分清 <strong>谁做决定、谁记事实、谁批准执行</strong>。</p></header>
<div class="toolbar"><div class="modes" aria-label="讲解场景"><button data-mode="data" aria-pressed="true">普通数据检查</button><button data-mode="training" aria-pressed="false">受治理训练</button></div>
<button id="tour" aria-pressed="true">暂停导览</button></div>
${plate}
<nav class="steps" aria-label="讲解步骤">${STAGES.map((s, i) => `<button data-step="${i}" aria-current="${i === 0 ? "step" : "false"}"><span>0${i + 1}</span>${s.label}</button>`).join("")}</nav>
<section class="explanation" aria-labelledby="step-title">
<div class="owner" id="owner">${STAGES[0].owner}</div><h2 id="step-title">${STAGES[0].title}</h2><p id="step-body">${STAGES[0].body}</p><p class="fact" id="step-fact">${STAGES[0].fact}</p>
<div id="training-options" hidden><div class="checks"><label><input id="confirmed" type="checkbox">演示：具体方案已确认</label><label><input id="governed" type="checkbox">演示：治理条件全部满足</label></div><p id="gate-status" role="status"></p><small>仅修改当前讲解页；不会发起训练或签发真实授权。</small></div></section>
<section class="ownership"><div class="section-title"><span>职责速查</span><span>一个 Agent Runtime，多个独立组件</span></div>
<div class="role-row"><b>原生 Codex</b><p>理解目标、调用模型、选择 Skill 与工具、决定下一步。</p><code>Agent Loop · Thread · Turn</code></div>
<div class="role-row"><b>codex-app-server Host</b><p>托管 Codex 进程，提供网页聊天、审批、事件与通用恢复。</p><code>agents/codex-app-server/</code></div>
<div class="role-row"><b>Galatea 任务 App</b><p>Task Service 保存任务、阶段与证据；App 展示；Worker 观察和投递接续。</p><code>agents/galatea-app/</code></div>
<div class="role-row"><b>Galatea 训练治理</b><p>校验 Release、数据、执行身份与资源范围，通过相应入口提交和核验。</p><code>项目 YAML 入口 / Campaign MCP</code></div>
<div class="role-row"><b>执行与存储</b><p>Ray 执行；MLflow 记录实验与模型证据；MinIO/S3 保存对象。</p><code>Ray · MLflow · MinIO</code></div></section>
<section class="routes"><h2>训练治理有两条独立入口。</h2>
<div class="route-grid"><article><span class="eyebrow">当前项目使用</span><h3>项目 YAML + 固定入口</h3><p><code>galatea.project.yaml</code> 声明契约；<code>job/governed.mjs</code> 调用 <code>packages/galatea-governance</code>，校验不可变 Release、readiness 和执行证据后进入 Ray。</p></article>
<article><span class="eyebrow">显式配置的另一条路线</span><h3>独立 Campaign MCP</h3><p><code>services/galatea-mcp</code> 自己管理项目、Campaign、预算、幂等与证据，通过原生 MCP 客户端接入。配置连接不等于获得业务授权。</p></article></div>
<p class="fact">两条路线的 Plan、Release、状态与授权不能互换。Host 的原生权限、Task 的训练确认、治理授权也各有范围。</p></section>
<section class="status"><h2>你当前实现到了哪里？</h2><p>当前架构文档与源码已包含通用 Host、聊天绑定 Task、自动展开 App、产物核验和 Worker 接续。验收记录记载：普通 CSV 需求在主入口 18880 完成真实会话链路。</p>
<p>真实猫狗 smoke 的执行与导入展示另有记录；它不等于“从 App 确认到完整训练交付”的链路已全部验收。本页依据当前源码和已有记录，不是一次新的部署健康检查。</p></section>
<details class="sources"><summary>展开源码依据 · 8 个核对入口</summary><ol>${sourceRows}</ol></details>
<footer>离线交互讲解 · 无外部请求 · 无模型调用 · 无训练提交<br>图形：Anatomy Figure / 工单检验机</footer>`;

const css = `
body{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI","Noto Sans CJK SC",sans-serif}
.iso-page{width:min(1060px,100% - 48px);margin:42px auto 60px}.eyebrow{font:10px/1.5 var(--anatomy-mono);letter-spacing:.12em;color:var(--anatomy-muted)}
.title-row{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}h1{font-size:clamp(30px,4.3vw,48px);letter-spacing:-.045em;line-height:1.25;font-weight:550;color:var(--anatomy-strong);margin:20px 0}
.intro{max-width:720px;font-size:15px;line-height:1.9;margin:0 0 28px}strong,b,h2,h3{color:var(--anatomy-strong)}
button{font:inherit;font-size:12px;background:transparent;border:1px solid var(--anatomy-faint);color:var(--anatomy-text);padding:9px 14px;cursor:pointer;transition:background .2s,color .2s}
button:hover{color:var(--anatomy-strong);border-color:var(--anatomy-mid)}button:focus-visible,.iso-stage:focus-visible{outline:2px solid var(--anatomy-hi);outline-offset:4px}
#theme{margin-top:24px;white-space:nowrap}.toolbar{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:16px}
.modes{display:flex}.modes button+button{margin-left:-1px}.modes button[aria-pressed="true"]{background:var(--anatomy-strong);color:var(--anatomy-page)}
.iso-plate{padding:30px 30px 40px}.iso-stage{touch-action:pan-y}.iso-legend{font-size:11px;line-height:1.8;margin-top:12px}.iso-caption{max-width:780px}.iso-plate-corner{font-size:10px}
.steps{display:grid;grid-template-columns:repeat(6,1fr);margin-top:26px}.steps button{text-align:left;border-width:0 0 1px;padding:12px 8px;font-size:12px}
.steps span{display:block;font:10px/2 var(--anatomy-mono);color:var(--anatomy-muted)}.steps button[aria-current="step"]{border-color:var(--anatomy-strong);color:var(--anatomy-strong)}
.explanation{padding:25px 0 30px;min-height:230px}.owner{font:11px/1.6 var(--anatomy-mono);color:var(--anatomy-muted);margin-bottom:6px}
h2{font-size:21px;font-weight:550;line-height:1.5;margin:8px 0 12px}p{line-height:1.85;margin:9px 0}
.explanation>p{max-width:880px}.fact{border-left:2px solid var(--anatomy-mid);padding-left:14px;font-size:12px;line-height:1.85}
.checks{display:flex;flex-wrap:wrap;gap:14px 26px;margin-top:20px}.checks label{display:flex;align-items:center;gap:7px;font-size:13px;cursor:pointer}input{accent-color:var(--anatomy-strong);width:16px;height:16px}
#gate-status{font-size:12px}small{font-size:11px;color:var(--anatomy-muted)}
.ownership,.routes,.status,.sources{border-top:1px solid var(--anatomy-faint);padding:25px 0}
.section-title{display:flex;justify-content:space-between;gap:16px;color:var(--anatomy-muted);font-size:11px;margin-bottom:18px}
.role-row{display:grid;grid-template-columns:180px 1fr 280px;gap:18px;align-items:start;padding:14px 0;border-bottom:1px solid var(--anatomy-faint);font-size:12px}
.role-row p{margin:0}.role-row code{font-size:10px;line-height:1.8;color:var(--anatomy-muted);overflow-wrap:anywhere}.role-row:last-child{border:0}
code{font-family:var(--anatomy-mono);font-size:.88em;overflow-wrap:anywhere}.route-grid{display:grid;grid-template-columns:1fr 1fr;gap:42px;margin:24px 0}
h3{font-size:16px;font-weight:550;margin:5px 0}.route-grid p,.status p{font-size:13px}.status p{max-width:900px}
summary{cursor:pointer;font-size:12px}.sources ol{padding-left:20px;display:grid;grid-template-columns:1fr 1fr;gap:20px}.sources li>*{display:block;font-size:11px;line-height:1.7}.sources code{margin:4px 0;color:var(--anatomy-muted)}
footer{font-size:10px;line-height:1.9;color:var(--anatomy-muted);margin-top:15px}
@media(max-width:700px){.iso-page{width:calc(100% - 32px);margin-top:25px}.title-row{display:block;position:relative}h1{font-size:30px}.intro{font-size:13px}
.iso-plate{padding:48px 3px 65px}.iso-plate-corners{inset:17px 13px}.iso-plate-corner[data-corner="hint"]{bottom:18px;font-size:9px}.iso-plate-corner[data-corner="readout"]{bottom:0}
.steps{grid-template-columns:repeat(3,1fr);gap:3px 10px}.steps button{padding:8px;font-size:12px}.role-row{grid-template-columns:1fr;gap:5px;padding:18px 0}.role-row code{font-size:10px}
.section-title{display:block}.section-title span{display:block;margin-top:3px}.route-grid,.sources ol{grid-template-columns:1fr;gap:22px}.explanation{min-height:300px}h2{font-size:19px}
#theme{position:absolute;right:0;top:-40px;margin:0;padding:5px 7px;font-size:10px}.toolbar button{padding:8px 11px;font-size:11px}.iso-keys{gap:5px 10px}.iso-caption{font-size:10px}}
@media(prefers-reduced-motion:reduce){button{transition:none}}
`;
const live = `
const STAGES = ${JSON.stringify(STAGES)}, STOPS = ${JSON.stringify(STOPS)};
const allowedStage = ${allowedStage.toString()};
const stageContent = ${stageContent.toString()};
const parts = ${JSON.stringify(machineData)}.map(p => ({...p, el: document.getElementById(p.id)}));
const vector = ${JSON.stringify(vector)};
const group = document.getElementById("machine-parts"), stage = document.querySelector(".iso-stage");
const steps = [...document.querySelectorAll("[data-step]")], modes = [...document.querySelectorAll("[data-mode]")];
const readout = document.querySelector("[data-readout]"), owner = document.getElementById("owner");
const title = document.getElementById("step-title"), body = document.getElementById("step-body"), fact = document.getElementById("step-fact");
const confirmed = document.getElementById("confirmed"), governed = document.getElementById("governed"), tourButton = document.getElementById("tour");
const still = matchMedia("(prefers-reduced-motion: reduce)");
let mode = "data", selected = 0, share = 0, target = 0, visible = false, frame = 0, last = 0;
let playing = !still.matches, pauseUntil = 0, clock = 0, lastOrder = "", hovered = false, focused = false;
function updateContent() {
  const s = stageContent(selected, mode);
  owner.textContent = s.owner; title.textContent = s.title; body.textContent = s.body; fact.textContent = s.fact;
  steps.forEach((b,i) => b.setAttribute("aria-current", i === selected ? "step" : "false"));
  modes.forEach(b => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  document.getElementById("training-options").hidden = mode !== "training";
  document.getElementById("gate-status").textContent = confirmed.checked && governed.checked
    ? "演示条件满足，可以查看执行与交付步骤。"
    : "演示关卡关闭：需要同时满足方案确认与治理条件。";
  stage.setAttribute("aria-label", "架构讲解：" + s.label + "；" + s.title + " 用左右方向键切换。");
  document.body.dataset.step = String(selected); document.body.dataset.mode = mode;
  tourButton.textContent = playing ? "暂停导览" : "自动导览";
  tourButton.setAttribute("aria-pressed", String(playing));
}
function choose(i, manual = true) {
  selected = allowedStage(i, mode, confirmed.checked, governed.checked);
  target = STOPS[selected]; clock = 0;
  if (manual) pauseUntil = performance.now() + 12000;
  updateContent(); if (manual) kick();
}
function draw() {
  parts.forEach(p => { if(p.moving) p.el.setAttribute("transform", "translate(" + (vector[0]*share) + " " + (vector[1]*share) + ")"); });
  const ordered = [...parts].sort((a,b) => (a.key+a.lift*share)-(b.key+b.lift*share));
  const signature = ordered.map(p => p.id).join();
  if (signature !== lastOrder) { ordered.forEach(p => group.appendChild(p.el)); lastOrder = signature; }
  readout.textContent = Math.abs(share-target) > 0.002 ? "工单移向 · " + STAGES[selected].label : "0" + (selected+1) + " / 06 · " + STAGES[selected].label;
  stage.dataset.position = share.toFixed(5);
}
function tick(now) {
  frame = 0;
  const dt = last ? Math.min((now-last)/1000, 1/30) : 1/60; last = now;
  if (playing && !still.matches && now > pauseUntil && !hovered && !focused) {
    clock += dt;
    if(clock > 6) {
      const next = selected === 5 ? 0 : selected + 1;
      if (allowedStage(next, mode, confirmed.checked, governed.checked) !== selected) choose(next, false);
      else clock = 0;
    }
  }
  share = still.matches ? target : share+(target-share)*(1-Math.exp(-dt/0.28));
  if (Math.abs(share-target)<0.00001) share=target;
  draw();
  if(visible && !document.hidden && ((playing && !still.matches) || share!==target)) frame=requestAnimationFrame(tick);
}
function kick(){if(!frame && visible && !document.hidden){last=0;frame=requestAnimationFrame(tick);}}
steps.forEach(b=>b.addEventListener("click",()=>choose(Number(b.dataset.step))));
modes.forEach(b=>b.addEventListener("click",()=>{mode=b.dataset.mode;choose(selected);}));
[confirmed,governed].forEach(b=>b.addEventListener("change",()=>choose(selected)));
tourButton.addEventListener("click",()=>{playing=!playing;pauseUntil=0;clock=0;updateContent();kick();});
document.getElementById("theme").addEventListener("click",()=>{document.body.dataset.theme=document.body.dataset.theme==="light"?"dark":"light";});
stage.addEventListener("keydown",e=>{
  if(["ArrowRight","ArrowLeft","Home","End"," "].includes(e.key)){
    e.preventDefault();
    if(e.key===" "){playing=!playing;updateContent();kick();}
    else choose(e.key==="Home"?0:e.key==="End"?5:selected+(e.key==="ArrowRight"?1:-1));
  }
});
stage.addEventListener("pointerenter",()=>{hovered=true;});
stage.addEventListener("pointerleave",()=>{hovered=false;pauseUntil=performance.now()+3600;kick();});
stage.addEventListener("focus",()=>{focused=true;});
stage.addEventListener("blur",()=>{focused=false;pauseUntil=performance.now()+3600;kick();});
new IntersectionObserver(entries=>{
  visible=entries[entries.length-1].isIntersecting;
  if(visible)kick();else{cancelAnimationFrame(frame);frame=0;last=0;}
},{rootMargin:"120px 0px"}).observe(stage);
document.addEventListener("visibilitychange",()=>{if(document.hidden){cancelAnimationFrame(frame);frame=0;last=0;}else kick();});
still.addEventListener("change",()=>{if(still.matches)playing=false;updateContent();kick();});
updateContent();draw();
`;
const html = k.pageHtml({
  title: "Codex × Galatea｜当前架构交互讲解",
  theme, width: 1060, body: content, script: live,
}).replace('<html lang="en">', '<html lang="zh-CN">').replace("</head>", `<style>${css}</style></head>`);
const filename = theme === "light" ? "index-light.html" : "index.html";
writeFileSync(resolve(HERE, filename), html);
writeFileSync(resolve(HERE, `machine-${theme}.svg`), k.figureSvg({ width: W, height: H, label: "Codex 与 Galatea 工单检验机", body, standalone: theme }));
console.log(`${filename}: ${R.items.length} solids; ${R.items.reduce((n, i) => n + (i.svg.match(/<path/g) || []).length, 0)} paths`);
A.auditOrExit(R, P, {
  frames: Array.from({ length: 61 }, (_, i) => i / 60),
  apart: true,
});
