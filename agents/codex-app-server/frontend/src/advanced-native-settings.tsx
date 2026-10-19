import { useState } from "react";
import { NativeOperation } from "./native-operation";
import { consoleStore as store, type ConsoleState } from "./store";

const groups = [
  { title: "聊天创建与运行上下文", operations: [
    ["thread/start", "创建高级聊天", "选择模型、指令和执行环境，也可创建不保留历史的临时聊天。"],
    ["thread/fork", "分叉当前聊天", "从当前聊天创建分支，选择保留的历史和运行环境。"],
    ["thread/resume", "恢复并配置当前聊天", "继续当前聊天，并调整恢复时使用的模型和运行设置。"],
    ["thread/settings/update", "修改聊天运行设置", "用于当前聊天，默认配置在“配置”中单独管理。"],
    ["turn/settings/update", "修改当前轮次设置", "仅影响仍在运行的当前轮次。"],
  ] },
  { title: "轮次、结构化输出与审查", operations: [
    ["turn/start", "启动高级轮次", "设置多模态输入、结构化输出、上下文和本轮使用的插件。"],
    ["turn/steer", "引导当前轮次", "向正在执行的轮次补充输入和要求。"],
    ["review/start", "启动高级代码审查", "选择审查目标，在当前聊天或独立聊天中查看结果。"],
  ] },
  { title: "插件共享", operations: [["plugin/share/updateTargets", "更新共享插件的可见范围", "使用远端插件 ID 更新共享对象，无需本地检出。"]] },
  { title: "存储维护", operations: [["rollout/compress", "压缩原生会话存储", "原生异步调度压缩。确认回执仅表示已接收，不能作为压缩完成的证明。"]] },
] as const;
export function AdvancedNativeSettings({ state, scope = "global" }: { state: ConsoleState; scope?: "global" | "thread" }) {
  const [open, setOpen] = useState(""); const session = state.sessions.find((s) => s.id === state.active);
  const turnId = session?.active_turn_id;
  return <section className="capability-page advanced-native-settings"><h3>{scope === "thread" ? "高级会话操作" : "高级原生能力"}</h3><p className="muted">{scope === "thread" ? "配置当前聊天、轮次和代码审查。" : "管理插件共享和原生存储。"}展开一项以配置并执行。</p>
    {scope === "thread" && <div className="settings-context"><span>当前聊天</span><span>{session?.name || (session ? "未命名聊天" : "未选择")}</span></div>}
    {groups.filter((group) => scope === "thread" ? group.operations.some(([method]) => method.startsWith("thread/") || method.startsWith("turn/")) : group.operations.every(([method]) => !method.startsWith("thread/") && !method.startsWith("turn/"))).map((group) => <section className="settings-section" key={group.title}><h4>{group.title}</h4>{group.operations.map(([method, title, description]) => {
      const needsThread = method !== "thread/start" && (method.startsWith("thread/") || method.startsWith("turn/") || method === "review/start");
      const needsTurn = ["turn/steer", "turn/settings/update"].includes(method);
      const context = needsThread ? { threadId: session?.thread_id, ...(needsTurn ? { [method === "turn/steer" ? "expectedTurnId" : "turnId"]: turnId } : {}) } : {};
      const managed = method === "thread/start" ? ["dynamicTools"] : method === "thread/resume" ? ["history", "path"] : [];
      return <details className="settings-disclosure" key={method} open={open === method} onToggle={(e) => { if (e.currentTarget.open) setOpen(method); else if (open === method) setOpen(""); }}><summary><span className="settings-disclosure-label"><span>{title}</span><small>{description}</small></span></summary>{open === method && <div className="settings-disclosure-body">{needsThread && !session?.thread_id || needsTurn && !turnId ? <p className="settings-empty">此操作需要{needsTurn ? "运行中的轮次" : "选择一个聊天"}。</p> : <NativeOperation key={`${method}:${session?.thread_id}:${turnId}`} {...{ state, method, title, context }} showHeading={false} transform={(schema) => ({ ...schema, properties: Object.fromEntries(Object.entries(schema.properties || {}).filter(([key]) => !managed.includes(key))) })} onResult={async () => { await store.refreshSessions(); }} />}</div>}</details>;
    })}</section>)}
  </section>;
}
