import { deferred } from "./deferred";
import { consoleStore as store, type ConsoleState } from "./store";
import { NativeOperation } from "./native-operation";
import { Execution, NativeEvents } from "./inspector";
const ModelSettings = deferred(() => import("./model-settings").then(m => ({ default: m.ModelSettings })));
const ThreadResources = deferred(() => import("./thread-resources").then(m => ({ default: m.ThreadResources })));
const AdvancedNativeSettings = deferred(() => import("./advanced-native-settings").then(m => ({ default: m.AdvancedNativeSettings })));
const ThreadMemory = deferred(() => import("./runtime-settings").then(m => ({ default: m.ThreadMemory })));
const ThreadHooks = deferred(() => import("./runtime-settings").then(m => ({ default: m.ThreadHooks })));
const Feedback = deferred(() => import("./runtime-settings").then(m => ({ default: m.DiagnosticsPanel })));
const Skills = deferred(() => import("./integrations").then(m => ({ default: m.SkillsPanel })));
const Mcp = deferred(() => import("./integrations").then(m => ({ default: m.McpPanel })));
const Apps = deferred(() => import("./apps-panel").then(m => ({ default: m.AppsPanel })));
const Voice = deferred(() => import("./voice-settings").then(m => ({ default: m.VoiceSettings })));

export const sessionPanelNames: Record<string, string> = {
  settings: "模型与权限", resources: "聊天资源", advanced: "高级会话操作", memory: "会话记忆",
  hooks: "Hooks 执行记录", loop: "执行流程", native: "原生事件", skills: "Skills", mcp: "MCP", apps: "Apps",
  plugins: "会话插件", voice: "会话语音上下文", feedback: "会话反馈",
};
export function SessionPanel({ tab, state }: { tab: string; state: ConsoleState }) {
  const session = state.sessions.find(s => s.id === state.active);
  if (!session?.thread_id && !["settings", "advanced", "skills", "apps", "feedback", "mcp"].includes(tab)) return <p>请先开始聊天。</p>;
  if (tab === "settings") return <ModelSettings state={state} />;
  if (tab === "resources") return <ThreadResources state={state} />;
  if (tab === "advanced") return <AdvancedNativeSettings state={state} scope="thread" />;
  if (tab === "memory") return <ThreadMemory state={state} />;
  if (tab === "hooks") return <ThreadHooks state={state} />;
  if (tab === "feedback") return <Feedback state={state} thread />;
  if (tab === "skills") return <Skills state={state} scope="thread" />;
  if (tab === "mcp") return <Mcp state={state} scope="thread" />;
  if (tab === "apps") return <Apps state={state} scope="thread" />;
  if (tab === "voice") return <Voice state={state} thread />;
  if (tab === "plugins") return <section className="capability-page"><h3>会话插件</h3><p className="muted">设置此聊天禁用的插件 ID，不影响其他聊天的安装或启用状态。</p>
    <NativeOperation state={state} method="thread/settings/update" title="应用会话插件" context={{ threadId: session?.thread_id }} initial={{ disabledPluginIds: session?.settings?.disabledPluginIds || [] }} onResult={async () => { await store.refreshSessions(); }}
      transform={schema => ({ ...schema, properties: { disabledPluginIds: schema.properties?.disabledPluginIds } })} />
  </section>;
  return <section className="capability-page"><h3>{sessionPanelNames[tab]}</h3>{tab === "loop" ? <Execution state={state} /> : <NativeEvents state={state} />}</section>;
}
