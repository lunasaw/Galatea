import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { Popover } from "radix-ui";
import { X } from "lucide-react";
import { consoleStore as store, busyStatuses, statusLabel, type ConsoleState } from "./store";
import { useResource } from "./capability-controls";
import { readAccountUsage, rateLimitBuckets } from "./account-usage-data";
import { contextUsage, contextWarning } from "./context-usage";
import "./context-status.css";

export function ContextRing({ percent }: { percent: number | null }) {
  return <svg viewBox="0 0 32 32" className="context-ring" aria-hidden="true">
    <circle className="context-ring-track" cx="16" cy="16" r="12" />
    {percent !== null && <circle className="context-ring-fill" cx="16" cy="16" r="12" pathLength="100" strokeDasharray={`${percent} 100`} transform="rotate(-90 16 16)" />}
  </svg>;
}
function StatusDetails({ state, close }: { state: ConsoleState; close: () => void }) {
  const session = state.sessions.find(s => s.id === state.active);
  const usage = contextUsage(state.snapshot);
  const warning = contextWarning(usage.percent);
  const account = useResource(useCallback(readAccountUsage, []));
  const busy = state.submitting || !!session?.active_turn_id || busyStatuses.has(session?.status || "");
  const compacting = state.snapshot?.steps?.some(step => step.type === "contextCompaction" && step.status === "inProgress");
  return <>
    <header><strong>会话状态</strong><button type="button" aria-label="关闭会话状态" onClick={close}><X size={16} /></button></header>
    <div className="context-status-summary"><ContextRing percent={usage.percent} />
      <div><strong>{usage.percent === null ? "上下文用量待更新" : `已使用 ${usage.percent}% · 剩余 ${100 - usage.percent}%`}</strong>
        <p>{usage.used === null ? "发送消息后显示原生用量" : `${usage.used.toLocaleString()} / ${usage.window?.toLocaleString() ?? "未知容量"} Token`}</p></div></div>
    {usage.percent !== null && <div className="context-meter" role="progressbar" aria-label="上下文占用" aria-valuenow={usage.percent} aria-valuemin={0} aria-valuemax={100}>
      <span style={{ width: `${usage.percent}%` }} /></div>}
    {warning && <p className="context-status-note context-warning">{warning}</p>}
    {compacting && <p className="context-status-note" role="status">Codex 正在压缩上下文，用量将在原生状态更新后刷新。</p>}
    <dl className="context-status-facts">
      <div><dt>聊天 ID</dt><dd>{session?.thread_id || "尚未开始聊天"}</dd></div>
      <div><dt>状态</dt><dd>{statusLabel(session?.status || "ready")}</dd></div>
      <div><dt>模型</dt><dd>{store.composerSettings.model || "默认"}</dd></div>
      <div><dt>工作目录</dt><dd>{store.cwd}</dd></div>
      {usage.total !== null && <div><dt>累计 Token</dt><dd>{usage.total.toLocaleString()}</dd></div>}
    </dl>
    <section className="context-rate-limits"><h4>账户速率限制</h4>
      {rateLimitBuckets(account.data?.limits).map((bucket, i) => <div key={bucket.limitId || i}>{[bucket.primary, bucket.secondary].filter(Boolean).map((window, j) => <p key={j}>{bucket.limitName || "账户"} · {window.windowDurationMins ? `${window.windowDurationMins} 分钟` : j ? "长期" : "短期"}：已使用 {window.usedPercent}%{window.resetsAt != null && <small>重置于 {new Date(window.resetsAt * 1000).toLocaleString()}</small>}</p>)}</div>)}
      {!rateLimitBuckets(account.data?.limits).length && <p>{account.loading ? "正在读取…" : account.data?.account?.type === "apiKey" ? "API 密钥账户请在服务商控制台查看额度。" : account.error || account.data?.errors.length ? "暂时无法读取账户额度。" : "当前连接未提供额度。"}</p>}
    </section>
    <footer><button type="button" disabled={!session?.thread_id || busy || compacting} onClick={() => { close(); void store.send("/compact"); }}>压缩上下文</button>
      <button type="button" onClick={() => { close(); window.dispatchEvent(new CustomEvent("codex-panel", { detail: "session:settings" })); }}>模型与权限</button></footer>
  </>;
}
export function ComposerContextStatus() {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const [open, setOpen] = useState(false);
  const usage = contextUsage(state.snapshot);
  const warning = contextWarning(usage.percent);
  useEffect(() => { const show = () => setOpen(true); window.addEventListener("codex-status", show); return () => window.removeEventListener("codex-status", show); }, []);
  useEffect(() => setOpen(false), [state.active]);
  const title = usage.percent === null ? "查看会话状态 · 上下文用量待更新" : `上下文已使用 ${usage.percent}%${warning ? ` · ${warning}` : ""}`;
  return <div className="composer-context-controls">
    <button type="button" className="composer-slash-trigger" aria-label="快捷命令" title="快捷命令 /" onClick={() => {
      document.querySelector<HTMLTextAreaElement>("#text")?.focus();
      window.dispatchEvent(new Event("codex-slash"));
    }}>/</button>
    <Popover.Root open={open} onOpenChange={setOpen}><Popover.Trigger asChild>
      <button type="button" className="composer-context-trigger" aria-label={title} title={title} data-warning={!!warning}>
        <ContextRing percent={usage.percent} /><span>{usage.percent === null ? "—" : `${usage.percent}%`}</span>
      </button></Popover.Trigger>
      <Popover.Portal><Popover.Content className="context-status-popover" side="top" align="start" sideOffset={12} collisionPadding={12} aria-label="会话状态" onCloseAutoFocus={event => { event.preventDefault(); document.querySelector<HTMLTextAreaElement>("#text")?.focus(); }}>
        {open && <StatusDetails state={state} close={() => setOpen(false)} />}
      </Popover.Content></Popover.Portal>
    </Popover.Root>
  </div>;
}
