import { deferred } from "./deferred";
const RuntimeSettings = deferred(() => import("./runtime-settings").then(m => ({ default: m.RuntimeSettings })));
const VoiceSettings = deferred(() => import("./voice-settings").then(m => ({ default: m.VoiceSettings })));
const AccountUsage = deferred(() => import("./account-usage").then(m => ({ default: m.AccountUsage })));
import "./csp-config";
import {
  requestThreadAction,
  ThreadActionDialog,
  type ThreadAction,
} from "./thread-actions";
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import { BRAND_NAME } from "./brand";
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  SimpleImageAttachmentAdapter,
  SimpleTextAttachmentAdapter,
  CompositeAttachmentAdapter,
} from "@assistant-ui/react";
import {
  Moon,
  Sun,
  Terminal,
  Workflow,
  Wrench,
  Settings2,
  X,
  MoreHorizontal,
  Folder,
  Puzzle,
  History,
  Bell,
  Code2,
  BookOpen,
  CircleHelp,
  PanelBottom,
  PanelRight,
  Info,
  FileDiff,
  Home,
  Search,
  Target,
  ArrowLeft,
  AudioLines,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogClose,
} from "./components/ui/dialog";
import { openSettings } from "./chat-controls";
import { ChatSearch } from "./chat-search";
import { attachments, nativeParts } from "./composer-input";
import { Thread } from "./components/assistant-ui/elements/thread.aui";
import { ThreadListSidebar } from "./components/assistant-ui/elements/threadlist-sidebar.aui";
import { TooltipIconButton } from "./components/assistant-ui/elements/tooltip-icon-button";
import {
  SidebarInset,
  SidebarTrigger,
  useSidebar,
} from "./components/ui/sidebar";
import { ResizableSidebarProvider } from "./sidebar-resize";
import { Button } from "./components/ui/button";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "./components/ui/dropdown-menu";
import { Capabilities, Execution, NativeEvents } from "./inspector";
import {
  NativeControl,
  NativeRequests,
  ConversationHistory,
  useNativeRequestPolling,
} from "./native-ui";
import { native } from "./native";



import { slashCommands } from "./slash-commands";
import { CodexToolFallback } from "./tool-ui";
import { consoleStore as store, busyStatuses, statusLabel } from "./store";
import { toConversationMessages } from "./messages";
import { WorkspaceFileScope } from "./workspace-file";
import { BottomPanel, WorkbenchPanel, type WorkbenchTab } from "./workbench";
import "./style.css";
import "./workbench.css";
import "./desktop.css";

const suggestions = [
  {
    title: "探索代码",
    label: "了解项目结构",
    prompt: "帮我了解这个项目的结构和关键模块，先从 README 和入口代码开始。",
  },
  {
    title: "修复问题",
    label: "定位错误并验证",
    prompt: "帮我排查一个问题，先检查相关代码和现有测试。",
  },
  {
    title: "审查更改",
    label: "检查未提交的修改",
    prompt: "审查当前未提交的代码修改，重点关注正确性和回归风险。",
  },
];
const settingsSections = [
  { id: "settings", label: "模型与权限", icon: Settings2 },
  { id: "usage", label: "使用情况和计费", icon: Info },
  { id: "skills", label: "Skills", icon: BookOpen },
  { id: "mcp", label: "MCP", icon: Wrench },
  { id: "apps", label: "Apps", icon: Puzzle },
  { id: "plugins", label: "插件", icon: Puzzle },
  { id: "memory", label: "Memory", icon: BookOpen },
  { id: "voice", label: "语音", icon: AudioLines },
  { id: "hooks", label: "Hooks", icon: Workflow },
  { id: "remote", label: "远程控制", icon: Terminal },
  { id: "environments", label: "执行环境", icon: Folder },
  { id: "import", label: "导入外部配置", icon: Folder },
  { id: "windows", label: "Windows sandbox", icon: Settings2 },
  { id: "diagnostics", label: "诊断与反馈", icon: Info },
  { id: "terminal", label: "终端", icon: Terminal },
  { id: "help", label: "快捷命令", icon: CircleHelp },
  { id: "capabilities", label: "能力", icon: Wrench },
  { id: "loop", label: "执行流程", icon: Workflow },
  { id: "native", label: "原生事件", icon: Code2 },
  { id: "protocol", label: "开发者工具", icon: Code2 },
];
const threadComponents = { ToolFallback: CodexToolFallback };

function Workspace() {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const { isMobile, setOpenMobile, state: sidebarState } = useSidebar();
  const [panel, setPanel] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [settingsSearch, setSettingsSearch] = useState("");
  const [sidePanel, setSidePanel] = useState<WorkbenchTab | null>(null);
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [terminalMounted, setTerminalMounted] = useState(false);
  const showTerminal = (open: boolean) => {
    setTerminalOpen(open);
    if (open) setTerminalMounted(true);
  };
  useNativeRequestPolling();
  useEffect(() => {
    const locate = (event: Event) => {
      const { itemId } = (event as CustomEvent).detail;
      setPanel("");
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const target = [...document.querySelectorAll<HTMLElement>("[data-item-ids]")]
          .find((node) => JSON.parse(node.dataset.itemIds || "[]").includes(itemId));
        if (!target) { store.fail(new Error("消息尚未渲染，请重新定位")); return; }
        const expand = target.querySelector<HTMLButtonElement>('.activity-trigger[aria-expanded="false"]');
        expand?.click(); target.scrollIntoView({ block: "center", behavior: "smooth" });
        target.animate([{ background: "#dcecff" }, { background: "transparent" }], { duration: 2500 });
      }));
    };
    window.addEventListener("codex-locate-message", locate);
    return () => window.removeEventListener("codex-locate-message", locate);
  }, []);
  const [theme, setTheme] = useState(
    localStorage.getItem("codex-agent.theme") || "light",
  );
  const session = state.sessions.find((s) => s.id === state.active);
  const messages = useMemo(
    () => toConversationMessages(state.snapshot),
    [state.snapshot],
  );
  const threads = useMemo(
    () =>
      [...state.sessions]
        .filter((s) => !s.archived)
        .reverse()
        .map((s) => ({
          id: s.id,
          remoteId: s.id,
          title: store.title(s.id),
          status: "regular" as const,
          custom: {
            status: s.status,
            cwd: s.cwd || s.settings?.cwd,
            projectId: store.projectForSession(s.id)?.id,
          },
        })),
    [state.sessions, state.titles, state.projectLayout, state.nativeProtocol],
  );
  const choose = async (id: string | null) => {
    if (isMobile) setOpenMobile(false);
    await store.select(id);
  };
  const running =
    state.submitting ||
    (!!session && busyStatuses.has(session.status)) ||
    state.snapshot?.turns?.at(-1)?.status === "inProgress";
  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage: (message) => message,
    isRunning: !!running,
    isDisabled:
      state.global ||
      state.loading ||
      state.sessionRefreshing ||
      state.creating ||
      state.settingsBusy ||
      (!!session && !["ready", ...busyStatuses].includes(session.status)),
    isLoading: state.loading,
    suggestions: messages.length ? [] : suggestions,
    onNew: async (message) => {
      await store.send(
        [
          ...message.content,
          ...(message.attachments || []).flatMap((a) => a.content),
        ]
          .filter((p) => p.type === "text")
          .map((p) => p.text)
          .join("\n"),
        nativeParts([
          ...message.content,
          ...(message.attachments || []).flatMap((a) => a.content),
        ]).filter((part) => part.type !== "text"),
      );
    },
    onCancel: async () => {
      await store.action("interrupt");
    },
    adapters: {
      attachments,
      threadList: {
        threadId: state.active || undefined,
        threads,
        isLoading: state.sessionsLoading,
        onSwitchToNewThread: () => choose(null),
        onSwitchToThread: choose,
        onRename: (id, title) => store.rename(id, title),
      },
    },
  });
  useEffect(() => {
    void store.init();
    const close = () => store.dispose();
    window.addEventListener("pagehide", close);
    return () => {
      window.removeEventListener("pagehide", close);
      store.dispose();
    };
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem("codex-agent.theme", theme);
  }, [theme]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (
        (event.ctrlKey || event.metaKey) &&
        event.shiftKey &&
        event.key.toLowerCase() === "o"
      ) {
        event.preventDefault();
        void runtime.threads.switchToNewThread();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [runtime]);
  const nativeAction = async (action: string) => {
    if (!session?.thread_id) return;
    if (action === "review") { setPanel(""); setSidePanel("codeReview"); return; }
    if (["fork", "archive", "delete", "undo", "rollback"].includes(action)) {
      requestThreadAction({
        action: action as ThreadAction,
        sessionId: session.id,
      });
      return;
    }
    try {
      const method = (
        {
          compact: "thread/compact/start",
          review: "review/start",
        } as Record<string, string>
      )[action];
      const result = await native(method, {
        threadId: session.thread_id,
        ...(action === "review"
          ? { target: { type: "uncommittedChanges" }, delivery: "inline" }
          : {}),
      });
      await store.refreshSessions();
      if (result.session) await store.select(result.session.id);
      else store.schedule();
    } catch (error) {
      store.fail(error);
    }
  };
  useEffect(() => {
    const panel = (event: Event) => {
      const name = (event as CustomEvent).detail;
      if (name === "search") { setPanel(""); setSearchOpen(true); return; }
      if (name === "model" || name === "reasoning") {
        setPanel(""); window.dispatchEvent(new Event("codex-model")); return;
      }
      if (name === "permissions") {
        window.dispatchEvent(new Event("codex-permissions"));
        return;
      }
      if (name === "goal" || name === "queue") {
        setPanel("");
        window.dispatchEvent(new CustomEvent("codex-composer-panel", { detail: name }));
        return;
      }
      if (name === "files") {
        setSidePanel("files");
        if (isMobile) setOpenMobile(false);
        return;
      }
      if (name === "background") { setPanel(""); setSidePanel("background"); return; }
      if (name === "review") { setPanel(""); setSidePanel("codeReview"); return; }
      if (name === "agents") { setPanel(""); setSidePanel("agents"); return; }
      if (name === "terminal") {
        showTerminal(true);
        if (isMobile) setOpenMobile(false);
        return;
      }
      setPanel(
        (
          {
            model: "settings",
            permissions: "settings",
            plan: "settings",
            status: "loop",
            resume: "history",
            feedback: "diagnostics",
          } as Record<string, string>
        )[name] || name,
      );
      if (isMobile) setOpenMobile(false);
    };
    const action = (event: Event) =>
      void nativeAction((event as CustomEvent).detail);
    window.addEventListener("codex-panel", panel);
    window.addEventListener("codex-action", action);
    return () => {
      window.removeEventListener("codex-panel", panel);
      window.removeEventListener("codex-action", action);
    };
  }, [session, isMobile, setOpenMobile]);
  useEffect(() => {
    if (!state.nativeRequests.length) return;
    const title = document.title;
    document.title = `(${state.nativeRequests.length}) 需要回应 · ${BRAND_NAME}`;
    return () => {
      document.title = title;
    };
  }, [state.nativeRequests.length]);
  const connected = state.health?.host_ready && !state.streamError;
  const cwd = store.cwd;
  const projectName =
    store.currentProject?.name ||
    cwd.split("/").filter(Boolean).at(-1) ||
    "本地工作区";
  const otherRequests = state.nativeRequests.filter(
    (r) => r.session_id !== state.active || !state.active,
  );
  const panelTitle =
    panel === "requests"
      ? "等待你的回应"
      : settingsSections.find((s) => s.id === panel)?.label || "设置";
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadActionDialog />
      <nav className="desktop-rail" aria-label="主导航">
        <TooltipIconButton tooltip="新聊天" data-active={!panel} onClick={() => { setPanel(""); void choose(null); }}><Home /></TooltipIconButton>
        <TooltipIconButton tooltip="会话历史" onClick={() => openSettings("history")}><History /></TooltipIconButton>
        <TooltipIconButton tooltip="Skill 库" onClick={() => openSettings("skills")}><BookOpen /></TooltipIconButton>
        <TooltipIconButton tooltip="扩展管理" onClick={() => openSettings("plugins")}><Puzzle /></TooltipIconButton>
        <div className="desktop-rail-spacer" />
        <TooltipIconButton tooltip="打开设置" onClick={() => openSettings()}><Settings2 /></TooltipIconButton>
      </nav>
      <div className="chat-surface" hidden={!!panel}>
      <ThreadListSidebar
        headerActions={<TooltipIconButton tooltip="搜索聊天" onClick={() => openSettings("search")}><Search /></TooltipIconButton>}
        navigation={
          <div className="sidebar-navigation">
            <Button variant="ghost" onClick={() => openSettings("history")}>
              <History />
              恢复会话
            </Button>
            <Button variant="ghost" onClick={() => openSettings("skills")}>
              <BookOpen />
              Skills
            </Button>
          </div>
        }
        footer={
          <div className="sidebar-bottom">
            <div className="sidebar-project">
              <Folder />
              <div>
                <strong>{projectName}</strong>
                <span>
                  <i className={connected ? "connected" : ""} />
                  {state.streamError
                    ? "正在重新连接"
                    : connected
                      ? "本地工作区"
                      : "正在连接"}
                </span>
              </div>
            </div>
            <div className="flex items-center justify-between gap-1">
              <Button variant="ghost" size="sm" onClick={() => openSettings()}>
                <Settings2 />
                设置
              </Button>
              <TooltipIconButton
                tooltip={theme === "light" ? "切换深色主题" : "切换浅色主题"}
                onClick={() => setTheme(theme === "light" ? "dark" : "light")}
              >
                {theme === "light" ? <Moon /> : <Sun />}
              </TooltipIconButton>
            </div>
          </div>
        }
      />
      <SidebarInset className="h-dvh min-w-0 overflow-hidden">
        <header className="chat-header">
          {(isMobile || sidebarState === "collapsed") && (
            <SidebarTrigger aria-label="切换侧栏" />
          )}
          <div className="chat-header-heading">
            <span
              id="session-title"
              title={state.active || ""}
              className="chat-header-title"
            >
              {state.global
                ? "运行实例事件"
                : state.active
                  ? store.title(state.active)
                  : "新会话"}
            </span>
            <span className="chat-header-path" title={cwd}>
              {projectName}
            </span>
          </div>
          <div className="chat-header-tools">
            <TooltipIconButton
              tooltip="打开文件"
              aria-pressed={sidePanel === "files"}
              onClick={() =>
                setSidePanel(sidePanel === "files" ? null : "files")
              }
            >
              <Folder />
            </TooltipIconButton>
            <TooltipIconButton
              tooltip="打开终端"
              aria-pressed={terminalOpen}
              onClick={() => showTerminal(!terminalOpen)}
            >
              <PanelBottom />
            </TooltipIconButton>
            <TooltipIconButton
              tooltip="查看摘要"
              aria-pressed={sidePanel === "summary"}
              onClick={() =>
                setSidePanel(sidePanel === "summary" ? null : "summary")
              }
            >
              <Info />
            </TooltipIconButton>
            <TooltipIconButton
              tooltip="查看更改"
              aria-pressed={sidePanel === "review"}
              onClick={() =>
                setSidePanel(sidePanel === "review" ? null : "review")
              }
            >
              <FileDiff />
            </TooltipIconButton>
            <TooltipIconButton
              tooltip="切换任务面板"
              aria-pressed={!!sidePanel}
              onClick={() => setSidePanel(sidePanel ? null : "launcher")}
            >
              <PanelRight />
            </TooltipIconButton>
          </div>
          {!!otherRequests.length && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setPanel("requests")}
              aria-label="查看其他会话请求"
            >
              <Bell />
              {otherRequests.length}
            </Button>
          )}
          <span id="health" className="sr-only">
            {connected ? "已连接" : "连接中"}
          </span>
          {state.active && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <TooltipIconButton tooltip="会话操作">
                  <MoreHorizontal />
                </TooltipIconButton>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onSelect={() => openSettings("background")}><Terminal />Agent 后台终端</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => openSettings("review")}><FileDiff />代码审查与审批记录</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => openSettings("agents")}><Workflow />子 Agent</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => window.dispatchEvent(new CustomEvent("codex-voice", { detail: "history" }))}><History />语音记录</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => setPanel("loop")}>
                  <Workflow />
                  执行流程
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => setPanel("native")}>
                  <Code2 />
                  原生事件
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuLabel>
                  {statusLabel(session?.status || "unknown")}
                </DropdownMenuLabel>
                <DropdownMenuItem
                  onSelect={() => void store.action("interrupt")}
                >
                  中断
                </DropdownMenuItem>
                <DropdownMenuItem
                  disabled={session?.status === "closed"}
                  onSelect={() => void store.action("resume")}
                >
                  核对并恢复
                </DropdownMenuItem>
                <DropdownMenuItem
                  className="text-destructive"
                  disabled={session?.status === "closed"}
                  onSelect={() => void store.action("close")}
                >
                  关闭会话
                </DropdownMenuItem>
                {Object.entries({
                  fork: "分叉会话",
                  compact: "压缩上下文",
                  undo: "回退上一轮",
                  rollback: "回退到指定轮次",
                  delete: "删除会话",
                  archive: "归档会话",
                }).map(([action, label]) => (
                  <DropdownMenuItem
                    key={action}
                    disabled={
                      state.settingsBusy ||
                      !!session?.active_turn_id ||
                      busyStatuses.has(session?.status || "")
                    }
                    onSelect={() => void nativeAction(action)}
                  >
                    {label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </header>
        <div className="workbench-body">
          <div className="workbench-chat-column">
            <div className="shrink-0 space-y-1 px-4 text-xs">
              {state.reset && (
                <div
                  id="reset"
                  role="status"
                  className="flex items-center justify-between bg-muted p-2"
                >
                  服务已重启，上一进程的事件历史已清空。持久化会话仍可核对并恢复。
                  <TooltipIconButton
                    tooltip="关闭重启提示"
                    onClick={() => store.patch({ reset: false })}
                  >
                    <X />
                  </TooltipIconButton>
                </div>
              )}
              {state.streamError && (
                <p role="status" className="p-2">
                  {state.streamError}
                </p>
              )}
              {state.error && (
                <div
                  id="error"
                  role="alert"
                  className="flex items-center justify-between gap-2 bg-destructive/10 p-2 text-destructive"
                >
                  <span>{state.error}</span>
                  {state.sessionRefreshing && !state.loading && (
                    <Button size="sm" variant="outline" onClick={() => void store.latest()}>
                      重试加载
                    </Button>
                  )}
                  {store.canRetry && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={state.submitting}
                      onClick={() => void store.retrySend()}
                    >
                      重试发送
                    </Button>
                  )}
                  <TooltipIconButton
                    tooltip="关闭错误提示"
                    onClick={() => store.patch({ error: "" })}
                  >
                    <X />
                  </TooltipIconButton>
                </div>
              )}
              {session &&
                !["ready", ...busyStatuses].includes(session.status) && (
                  <div className="flex items-center gap-2 p-2">
                    {statusLabel(session.status)}
                    <Button
                      id="resume"
                      size="sm"
                      variant="outline"
                      disabled={session.status === "closed"}
                      onClick={() => void store.action("resume")}
                    >
                      核对并恢复
                    </Button>
                  </div>
                )}
              {!!messages.length &&
                (state.older ||
                  state.history ||
                  state.snapshot?.coverage.status !== "complete") && (
                  <div className="flex items-center justify-center gap-2 pt-1 text-muted-foreground">
                    <Button
                      id="older-messages"
                      variant="ghost"
                      size="xs"
                      disabled={!state.older}
                      onClick={() => void store.older()}
                    >
                      更早消息
                    </Button>
                    {state.history && (
                      <Button
                        id="latest-messages"
                        variant="ghost"
                        size="xs"
                        onClick={() => void store.latest()}
                      >
                        返回最新
                      </Button>
                    )}
                    <span>
                      {state.history
                        ? "已加载较早记录 · 实时更新中"
                        : state.snapshot?.coverage.status !== "complete"
                          ? "观察不完整：" +
                            (state.snapshot?.coverage.reason || "未知")
                          : "还有较早的执行记录"}
                    </span>
                  </div>
                )}
            </div>
            {state.global ? (
              <div className="flex flex-1 flex-col items-center justify-center gap-4 text-muted-foreground">
                <Terminal />
                <h1>运行实例事件</h1>
                <Button variant="outline" onClick={() => void choose(null)}>
                  开始新对话
                </Button>
              </div>
            ) : (
              <div className="min-h-0 flex-1">
                <WorkspaceFileScope cwd={cwd}>
                  <Thread
                    components={threadComponents}
                    interactions={<NativeRequests state={state} />}
                  />
                </WorkspaceFileScope>
              </div>
            )}
            {terminalMounted && (
              <BottomPanel
                state={state}
                open={terminalOpen}
                onClose={() => showTerminal(false)}
              />
            )}
          </div>
          <WorkbenchPanel
            tab={panel ? null : sidePanel}
            state={state}
            mobile={isMobile}
            onTab={setSidePanel}
            onClose={() => setSidePanel(null)}
          />
        </div>
      </SidebarInset>
      </div>
      {panel === "history" && <section className="conversation-history-page" aria-label="会话历史">
        <header><h1>会话历史</h1><Button variant="ghost" onClick={() => setPanel("")}><ArrowLeft />返回聊天</Button></header>
        <ConversationHistory state={state} onOpen={() => setPanel("")} />
      </section>}
      {!!panel && panel !== "history" && <section className="settings-page" aria-label="设置">
          <div className="settings-layout">
            <nav className="settings-navigation" aria-label="设置分类">
              <div className="settings-page-title"><h1>设置</h1><TooltipIconButton tooltip="返回聊天" onClick={() => setPanel("")}><ArrowLeft /></TooltipIconButton></div>
              <label className="settings-search"><Search size={15} /><input type="search" aria-label="搜索设置" placeholder="搜索" value={settingsSearch} onChange={(event) => setSettingsSearch(event.target.value)} /></label>
              {settingsSections.filter((section) =>
                (section.id !== "windows" || state.nativeProtocol?.platform?.startsWith("win")) &&
                section.label.toLowerCase().includes(settingsSearch.toLowerCase())
              ).map(({ id, label, icon: Icon }) => (
                <button type="button" key={id} data-active={panel === id} onClick={() => setPanel(id)}>
                  <Icon />{label}
                </button>
              ))}
            </nav>
            <div className="settings-body" key={panel}>
              {panel === "usage" ? <AccountUsage state={state} /> : panel === "voice" ? <VoiceSettings /> : ["memory", "hooks", "remote", "environments", "import", "windows", "diagnostics"].includes(panel) ? (
                <RuntimeSettings tab={panel} state={state} />
              ) : panel === "capabilities" ? (
                <Capabilities state={state} />
              ) : panel === "loop" ? (
                <Execution state={state} />
              ) : panel === "native" ? (
                <NativeEvents state={state} />
              ) : panel === "requests" ? (
                <NativeRequests state={state} other />
              ) : panel === "help" ? (
                <div className="command-help">
                  <h3>在对话中直接操作</h3>
                  <p>输入 / 选择命令，可用中文或命令名筛选；↑ ↓ 选择，Enter / Tab 确认，→ 打开子菜单，← / Esc 返回。</p>
                  <dl>
                    {slashCommands.map((command) => (
                      <div key={command.name}>
                        <dt>/{command.name}</dt>
                        <dd>{command.label}{command.unavailable && `（${command.unavailable}）`}</dd>
                      </div>
                    ))}
                  </dl>
                  <p>
                    Enter 发送 · Shift + Enter 换行
                    <br />
                    Ctrl / ⌘ + Shift + O 新建会话
                    <br />
                    运行时可继续输入补充说明，也可点击停止。
                  </p>
                </div>
              ) : (
                <NativeControl state={state} initialTab={panel} />
              )}
            </div>
          </div>
      </section>}
      <ChatSearch open={searchOpen} onOpenChange={setSearchOpen} state={state} />
    </AssistantRuntimeProvider>
  );
}
createRoot(document.getElementById("root")!).render(
  <ResizableSidebarProvider>
    <Workspace />
  </ResizableSidebarProvider>,
);
