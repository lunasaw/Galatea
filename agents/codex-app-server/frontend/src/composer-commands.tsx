import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type SyntheticEvent } from "react";
import { useAui } from "@assistant-ui/react";
import {
  Paperclip, Gauge, MessageCirclePlus, Share2, Split, File, CircleDashed,
  MessageSquare, Archive, Brain, Box, Plus, Lightbulb, Target, ListOrdered,
  Search, Shield, BookOpen, Puzzle, History, FileDiff, Undo2, Info, CircleHelp,
  ChevronLeft, ChevronRight, Check, Settings2,
} from "lucide-react";
import { allPages } from "./capability-controls";
import type { Obj } from "./native";
import { consoleStore as store, busyStatuses, type ConsoleState } from "./store";
import { requestThreadAction } from "./thread-actions";
import { contextUsedPercent, effortNames, exactSlashCommand, filterSlashCommands, type SlashCommand } from "./slash-commands";

const icons = { mcp: Paperclip, usage: Gauge, aside: MessageCirclePlus, share: Share2, fork: Split,
  init: File, compact: CircleDashed, feedback: MessageSquare, archive: Archive, reasoning: Brain,
  model: Box, new: Plus, plan: Lightbulb, goal: Target, queue: ListOrdered, search: Search,
  permissions: Shield, skills: BookOpen, apps: Puzzle, plugins: Puzzle, resume: History,
  review: FileDiff, undo: Undo2, status: Info, help: CircleHelp };
type Row = { id: string; label: string; description?: string; icon: typeof Check; checked?: boolean; submenu?: boolean; disabled?: string; run: () => void | Promise<void> };
export function useSlashCommands({ text, state, disabled }: { text: string; state: ConsoleState; disabled: boolean }) {
  const aui = useAui();
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [submenu, setSubmenu] = useState("");
  const [selected, setSelected] = useState(0);
  const [busy, setBusy] = useState(false);
  const [servers, setServers] = useState<Obj[]>();
  const [mcpError, setMcpError] = useState("");
  const input = useRef<HTMLTextAreaElement | null>(null);
  const menu = useRef<HTMLDivElement>(null);
  const lock = useRef(false);
  const id = useId();
  const session = state.sessions.find((row) => row.id === state.active);
  const matches = disabled ? [] : filterSlashCommands(text);
  const open = !dismissed && focused && matches.length > 0;
  const settings = store.composerSettings;
  const model = state.models.find((row) => row.model === settings.model);
  const used = contextUsedPercent(state.snapshot);
  const unavailable = (command: SlashCommand) => command.unavailable
    || (command.thread && !session?.thread_id ? "请先开始聊天" : "")
    || (state.creating || state.submitting || state.settingsBusy ? "正在处理，请稍候" : "")
    || (command.idle && (session?.active_turn_id || busyStatuses.has(session?.status || "")) ? "请先停止当前回复" : "");
  useEffect(() => { setDismissed(false); setSubmenu(""); setSelected(0); }, [text, state.active]);
  useEffect(() => {
    if (!open || submenu !== "mcp") return;
    let disposed = false;
    setServers(undefined); setMcpError("");
    void allPages("mcpServerStatus/list", session?.thread_id ? { threadId: session.thread_id } : {}).then((rows) => { if (!disposed) setServers(rows); })
      .catch((error) => { if (!disposed) { setMcpError(String(error)); setServers([]); } });
    return () => { disposed = true; };
  }, [open, submenu, session?.thread_id]);
  const close = () => {
    setDismissed(true); setSubmenu("");
    if (aui.composer().getState().text === text) aui.composer().setText("");
  };
  const showPanel = (name: string) => { close(); window.dispatchEvent(new CustomEvent("codex-panel", { detail: name })); };
  const execute = async (command: SlashCommand) => {
    if (unavailable(command)) return;
    if (command.submenu) { setSubmenu(command.name); setSelected(0); setFocused(true); input.current?.focus(); return; }
    await store.send(`/${command.name}`);
    if (!store.state.error) close();
  };
  const changeSetting = async (values: { model?: string; effort?: string }) => {
    if (await store.updateComposerSettings(values)) close();
  };
  const settingDisabled = state.creating || state.submitting || state.settingsBusy ? "正在处理，请稍候" : undefined;
  const rows: Row[] = submenu === "model" ? state.models.map((item) => ({
    id: item.model, label: item.displayName || item.model, description: item.model,
    icon: Box, checked: settings.model === item.model, disabled: settingDisabled,
    run: () => {
      const efforts = item.supportedReasoningEfforts?.map((row) => row.reasoningEffort) || [];
      return changeSetting({ model: item.model, effort: efforts.includes(settings.effort) ? settings.effort : item.defaultReasoningEffort || "" });
    },
  })) : submenu === "reasoning" ? (model?.supportedReasoningEfforts || []).map(({ reasoningEffort }) => ({
    id: reasoningEffort, label: effortNames[reasoningEffort] || reasoningEffort, icon: Brain,
    checked: reasoningEffort === settings.effort, disabled: settingDisabled, run: () => changeSetting({ effort: reasoningEffort }),
  })) : submenu === "mcp" ? [
    ...(servers || []).map((server) => ({ id: server.name, label: server.name,
      description: `${Object.keys(server.tools || {}).length} 个工具 · ${{ oAuth: "已授权", bearerToken: "令牌认证", notLoggedIn: "未登录", unsupported: "无需登录" }[server.authStatus as string] || server.authStatus || "状态未知"}`,
      icon: Paperclip, run: () => showPanel("mcp") })),
    { id: "mcp-settings", label: "MCP 设置", description: "管理服务器、工具和授权", icon: Settings2, run: () => showPanel("mcp") },
  ] : submenu === "fork" ? [
    { id: "fork-current", label: "从当前聊天创建分支", description: "包含完整聊天上下文", icon: Split, run: () => { close(); requestThreadAction({ action: "fork", sessionId: session?.id }); } },
    { id: "fork-turn", label: "从指定轮次创建分支", description: "选择要保留的最后一轮", icon: History, run: () => { close(); requestThreadAction({ action: "fork", sessionId: session?.id, chooseTurn: true }); } },
  ] : matches.map((command) => ({
    id: command.name, label: command.label, icon: icons[command.name as keyof typeof icons], submenu: command.submenu,
    description: command.name === "compact" && used !== null ? `${command.description}（已使用 ${used}%）`
      : command.name === "reasoning" ? effortNames[settings.effort] || settings.effort
      : command.name === "model" ? model?.displayName || settings.model : command.description,
    disabled: unavailable(command), run: () => execute(command),
  }));
  const index = rows.length ? selected % rows.length : 0;
  useLayoutEffect(() => {
    const popup = menu.current, anchor = popup?.parentElement;
    if (!open || !popup || !anchor) return;
    const fit = () => {
      const mobile = window.innerWidth < 768;
      const top = Math.max(12, document.querySelector(".chat-header")?.getBoundingClientRect().bottom || 0);
      popup.style.maxHeight = `${Math.max(80, Math.min(mobile ? 440 : 510, window.innerHeight * (mobile ? .52 : .58), anchor.getBoundingClientRect().top - top - 20))}px`;
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(anchor);
    window.addEventListener("resize", fit);
    return () => { observer.disconnect(); window.removeEventListener("resize", fit); };
  }, [open, state.active, state.snapshot?.messages?.length]);
  useEffect(() => { menu.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" }); }, [index, submenu]);
  const choose = async (row?: Row) => {
    if (!row || row.disabled || lock.current) return;
    lock.current = true; setBusy(true);
    try { await row.run(); } catch (error) { store.fail(error); }
    finally { lock.current = false; setBusy(false); }
  };
  const move = (direction: number) => {
    for (let count = 1; count <= rows.length; count++) {
      const next = (index + direction * count + rows.length) % rows.length;
      if (!rows[next].disabled) { setSelected(next); return; }
    }
  };
  const onKeyDownCapture = (event: KeyboardEvent) => {
    if (!open || event.nativeEvent.isComposing || event.keyCode === 229 || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return false;
    if (!["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home", "End", "Enter", "Tab", "Escape"].includes(event.key)) return false;
    if (event.key === "ArrowRight" && !rows[index]?.submenu || event.key === "ArrowLeft" && !submenu) return false;
    event.preventDefault(); event.stopPropagation();
    if (event.key === "Escape") { if (submenu) { setSubmenu(""); setSelected(0); } else setDismissed(true); }
    else if (event.key === "ArrowLeft") { setSubmenu(""); setSelected(0); }
    else if (["Enter", "Tab", "ArrowRight"].includes(event.key)) void choose(rows[index]);
    else if (event.key === "Home") setSelected(Math.max(0, rows.findIndex((row) => !row.disabled)));
    else if (event.key === "End") { const last = rows.map((row) => !row.disabled).lastIndexOf(true); setSelected(Math.max(0, last)); }
    else move(event.key === "ArrowDown" ? 1 : -1);
    return true;
  };
  const track = (event: SyntheticEvent<HTMLTextAreaElement>) => { input.current = event.currentTarget; setFocused(true); };
  return {
    onKeyDownCapture,
    submit: () => {
      if (disabled) return false;
      if (!dismissed && matches.length) { void choose(rows[index]); return true; }
      const command = exactSlashCommand(text);
      if (!command) return false;
      if (!unavailable(command)) void choose({ id: command.name, label: command.label, icon: Plus, run: () => execute(command) });
      else store.fail(new Error(unavailable(command)));
      return true;
    },
    inputProps: {
      onFocus: track, onInput: track, onBlur: () => setFocused(false),
      "aria-autocomplete": "list" as const,
      "aria-controls": open ? id : undefined,
      "aria-activedescendant": open && rows.length ? `${id}-${index}` : undefined,
      "aria-expanded": open,
    },
    menu: open ? <div className="slash-menu desktop-slash-menu" ref={menu} onMouseDown={(event) => event.preventDefault()}>
      {submenu && <button type="button" className="slash-back" aria-label="返回快捷命令" onClick={() => { setSubmenu(""); setSelected(0); input.current?.focus(); }}><ChevronLeft /><span>{{ mcp: "MCP 服务器", fork: "创建聊天分支", model: "模型", reasoning: "推理强度" }[submenu]}</span></button>}
      <div role="listbox" id={id} aria-label={submenu ? `${{ mcp: "MCP", fork: "聊天分支", model: "模型", reasoning: "推理" }[submenu]}选项` : "快捷命令"} aria-busy={busy || submenu === "mcp" && !servers}>
        {rows.map((row, i) => <button key={row.id} type="button" role="option" id={`${id}-${i}`}
          aria-selected={i === index} aria-disabled={!!row.disabled || busy} title={row.disabled || undefined}
          onMouseMove={() => { if (!row.disabled) setSelected(i); }} onClick={() => void choose(row)}>
          <row.icon className="slash-command-icon" aria-hidden="true" />
          <span className="slash-command-label">{row.label}</span>
          {row.description && <span className="slash-command-description">{row.description}</span>}
          {row.disabled && <span className="slash-command-unavailable">{row.disabled}</span>}
          {row.checked && <Check className="slash-command-trailing" aria-hidden="true" />}
          {row.submenu && <ChevronRight className="slash-command-trailing" aria-hidden="true" />}
        </button>)}
      </div>
      {submenu === "mcp" && (!servers ? <p role="status">正在读取 MCP 服务器状态…</p> : !servers.length && !mcpError ? <p role="status">尚未配置 MCP 服务器</p> : null)}
      {submenu && !rows.length && <p role="status">{submenu === "reasoning" ? "当前模型未提供可选推理强度" : "暂无可用模型"}</p>}
      {submenu === "mcp" && mcpError && <p role="alert">{mcpError}</p>}
    </div> : null,
  };
}
