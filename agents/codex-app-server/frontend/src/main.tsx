import { useActivatedApp } from "./activated-app";
import { conversationApp } from "./conversation-app";
import { SessionPanel, sessionPanelNames } from "./session-panels";
const ModelDefaults = deferred(() => import("./model-settings").then(m => ({ default: m.ModelDefaults })));
import { NativeNoticeCenter } from "./native-notice-center";
const AccountManagement = deferred(() => import("./account-management").then(m => ({ default: m.AccountManagement })));
const AdvancedNativeSettings = deferred(() => import("./advanced-native-settings").then(m => ({ default: m.AdvancedNativeSettings })));
import { deferred } from "./deferred";
const RuntimeSettings = deferred(() => import("./runtime-settings").then(m => ({ default: m.RuntimeSettings })));
const ConfigurationSettings = deferred(() => import("./configuration-settings").then(m => ({ default: m.ConfigurationSettings })));
const VoiceSettings = deferred(() => import("./voice-settings").then(m => ({ default: m.VoiceSettings })));
const AccountUsage = deferred(() => import("./account-usage").then(m => ({ default: m.AccountUsage })));
const ImageGenerationSettings = deferred(() => import("./image-generation-settings").then(m => ({ default: m.ImageGenerationSettings })));
import "./csp-config";
import {
  requestThreadAction,
  ThreadActionDialog,
  type ThreadAction,
} from "./thread-actions";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
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
  ListChecks,
  Home,
  Search,
  Target,
  ArrowLeft,
  AudioLines,
  Image,
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
import { ReviewPicker } from "./review-picker";
import { useThreadExport } from "./thread-export-menu";
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
import { Capabilities } from "./inspector";
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
import "./settings-shell.css";
import "./settings-content.css";

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
  { id: "configuration", label: "配置", icon: Settings2 },
  { id: "defaults", label: "模型与实验功能", icon: Settings2 },
  { id: "account", label: "账户与登录", icon: Info },
  { id: "advanced", label: "高级原生能力", icon: Wrench },
  { id: "usage", label: "使用情况和计费", icon: Info },
  { id: "skills", label: "Skills", icon: BookOpen },
  { id: "mcp", label: "MCP", icon: Wrench },
  { id: "apps", label: "Apps", icon: Puzzle },
  { id: "plugins", label: "插件", icon: Puzzle },
  { id: "memory", label: "Memory", icon: BookOpen },
  { id: "voice", label: "语音", icon: AudioLines },
  { id: "images", label: "图像生成", icon: Image },
  { id: "hooks", label: "Hooks", icon: Workflow },
  { id: "remote", label: "远程控制", icon: Terminal },
  { id: "environments", label: "执行环境", icon: Folder },
  { id: "import", label: "导入外部配置", icon: Folder },
  { id: "windows", label: "Windows sandbox", icon: Settings2 },
  { id: "diagnostics", label: "诊断与反馈", icon: Info },
  { id: "help", label: "快捷命令", icon: CircleHelp },
  { id: "capabilities", label: "能力", icon: Wrench },
  { id: "protocol", label: "开发者工具", icon: Code2 },
];
const threadComponents = { ToolFallback: CodexToolFallback };
const settingsGroups = [
  { label: "偏好设置", ids: ["configuration", "defaults", "account", "usage", "voice", "images"] },
  { label: "扩展与连接", ids: ["skills", "mcp", "apps", "plugins", "memory", "hooks"] },
  { label: "高级", ids: ["remote", "environments", "import", "windows", "advanced", "diagnostics", "help", "capabilities", "protocol"] },
];

function Workspace() {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const { isMobile, setOpenMobile, state: sidebarState } = useSidebar();
  const [panel, setPanel] = useState("");
  const [sessionPanel, setSessionPanel] = useState("");
  useEffect(() => setSessionPanel(""), [state.active]);
  const [searchOpen, setSearchOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  useEffect(() => setReviewOpen(false), [state.active]);
  const [settingsSearch, setSettingsSearch] = useState("");
  const settingsDialogRef = useRef<HTMLDivElement>(null);
  const [sidePanel, setSidePanel] = useState<WorkbenchTab | null>(null);
  const legacyActivation = useActivatedApp(state.active);
  const activation = conversationApp(state) || legacyActivation;
  const appBaseline = useRef<{ session: string | null; cursor?: string }>({ session: null });
  useEffect(() => {
    setSidePanel((current) => current === "apps" ? null : current);
  }, [state.active]);
  useEffect(() => {
    if (appBaseline.current.session !== state.active) appBaseline.current = { session: state.active };
    if (!state.snapshot || state.loading) return;
    // History establishes the baseline. Only a new App invocation in this
    // conversation may reveal the panel; loading history must stay quiet.
    if (!appBaseline.current.cursor) {
      appBaseline.current.cursor = state.snapshot.watermark;
      return;
    }
    const [epoch, sequence] = appBaseline.current.cursor.split(":");
    const [appEpoch, appSequence] = (activation?.event_cursor || "").split(":");
    if (epoch !== state.snapshot.epoch) {
      appBaseline.current.cursor = state.snapshot.watermark;
      return;
    }
    if (epoch === appEpoch && /^\d+$/.test(sequence) && /^\d+$/.test(appSequence)
        && BigInt(appSequence) > BigInt(sequence)) {
      appBaseline.current.cursor = activation!.event_cursor;
      setSidePanel("apps");
    }
  }, [activation?.activation_id, state.active, state.loading, !!state.snapshot]);
  useEffect(() => {
    const open = () => setSidePanel("apps");
    window.addEventListener("codex-open-app", open);
    return () => window.removeEventListener("codex-open-app", open);
  }, []);
  const showApps = async () => {
    if (!store.getSnapshot().active && !await store.create()) return;
    setSidePanel("apps");
  };
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
  const threadExport = useThreadExport(session, !state.loading && !state.sessionRefreshing && !!state.snapshot);
  const messages = useMemo(
    () => toConversationMessages(state.snapshot, state.global ? [] : state.pendingMessages.filter((row) => row.id === state.active)),
    [state.snapshot, state.pendingMessages, state.active, state.global],
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
    (state.submitting && (!state.pendingMessages.some((row) => row.status === "sending") ||
      state.pendingMessages.some((row) => row.status === "sending" && row.id === state.active))) ||
    (!!session && busyStatuses.has(session.status)) ||
    state.snapshot?.turns?.at(-1)?.status === "inProgress";
  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage: (message) => message,
    isRunning: !!running,
    isSendDisabled: state.submitting || state.creating,
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
    if (action === "review") { setPanel(""); setReviewOpen(true); return; }
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
        } as Record<string, string>
      )[action];
      const result = await native(method, {
        threadId: session.thread_id,
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
      const name = (event as CustomEvent).detail as string;
      setSessionPanel("");
      if (name === "status") { setPanel(""); window.dispatchEvent(new Event("codex-status")); return; }
      const sessionTab = name.startsWith("session:") ? name.slice(8) : ["settings", "resources", "loop", "native", "feedback"].includes(name) ? name : "";
      if (sessionTab === "apps") { setPanel(""); void showApps(); if (isMobile) setOpenMobile(false); return; }
      if (sessionTab && sessionPanelNames[sessionTab]) { setPanel(""); setSessionPanel(sessionTab); if (isMobile) setOpenMobile(false); return; }
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
      if (name === "review") { setPanel(""); setReviewOpen(true); if (isMobile) setOpenMobile(false); return; }
      if (name === "approvals") { setPanel(""); setSidePanel("approvals"); return; }
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
            resume: "history",
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
      <div className="chat-surface" hidden={panel === "history"}>
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
              tooltip="打开 Apps"
              aria-pressed={sidePanel === "apps"}
              onClick={() => void showApps()}
            >
              <Puzzle />
            </TooltipIconButton>
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
              <DropdownMenuContent align="end" className="thread-actions-menu" collisionPadding={8}
                onCloseAutoFocus={(event) => {
                  if (reviewOpen) {
                    event.preventDefault();
                    requestAnimationFrame(() => document.querySelector<HTMLElement>('.review-picker [role="listbox"]')?.focus());
                  }
                }}>
                {threadExport.menu}
                <DropdownMenuItem onSelect={() => openSettings("background")}><Terminal />Agent 后台终端</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => openSettings("review")}><FileDiff />审查更改</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => openSettings("approvals")}><ListChecks />审批记录</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => openSettings("agents")}><Workflow />子 Agent</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => window.dispatchEvent(new CustomEvent("codex-voice", { detail: "history" }))}><History />语音记录</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => openSettings("session:loop")}>
                  <Workflow />
                  执行流程
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => openSettings("session:native")}>
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
        {threadExport.status}
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
              <NativeNoticeCenter state={state} />
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
                  (state.snapshot && state.snapshot.coverage.status !== "complete")) && (
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
            tab={panel || sessionPanel ? null : sidePanel}
            activation={activation}
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
      <Dialog open={!!panel && panel !== "history"} onOpenChange={(open) => { if (!open) setPanel(""); }}>
        <DialogContent
          ref={settingsDialogRef}
          className="settings-page"
          showCloseButton={false}
          aria-describedby={undefined}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            settingsDialogRef.current?.focus();
          }}
        >
          <header className="settings-page-header">
            <DialogTitle>设置</DialogTitle>
            <TooltipIconButton tooltip="返回聊天" onClick={() => setPanel("")}><X /></TooltipIconButton>
          </header>
          <div className="settings-layout">
            <nav className="settings-navigation" aria-label="设置分类">
              <label className="settings-search"><Search size={16} /><input type="search" aria-label="搜索设置" placeholder="搜索设置" value={settingsSearch} onChange={(event) => setSettingsSearch(event.target.value)} /></label>
              <div className="settings-navigation-groups">
                {settingsGroups.map((group) => {
                  const sections = group.ids.flatMap((id) => settingsSections.filter((section) => section.id === id &&
                    (id !== "windows" || state.nativeProtocol?.platform?.startsWith("win")) &&
                    section.label.toLowerCase().includes(settingsSearch.trim().toLowerCase())));
                  return sections.length ? <div className="settings-navigation-group" key={group.label}>
                    <p>{group.label}</p>
                    {sections.map(({ id, label, icon: Icon }) => <button type="button" key={id} data-active={panel === id} aria-current={panel === id ? "page" : undefined} onClick={() => setPanel(id)}>
                      <Icon /><span>{label}</span>
                    </button>)}
                  </div> : null;
                })}
                {settingsSearch.trim() && !settingsSections.some((section) =>
                  (section.id !== "windows" || state.nativeProtocol?.platform?.startsWith("win")) && section.label.toLowerCase().includes(settingsSearch.trim().toLowerCase())
                ) && <p className="settings-search-empty" role="status">没有找到相关设置</p>}
              </div>
            </nav>
            <div className="settings-body" key={panel}>
              {panel === "images" ? <ImageGenerationSettings /> : panel === "account" ? <AccountManagement state={state} /> : panel === "advanced" ? <AdvancedNativeSettings state={state} /> : panel === "defaults" ? <ModelDefaults state={state} /> : panel === "configuration" ? <ConfigurationSettings state={state} /> : panel === "usage" ? <AccountUsage state={state} /> : panel === "voice" ? <VoiceSettings state={state} /> : ["memory", "hooks", "remote", "environments", "import", "windows", "diagnostics"].includes(panel) ? (
                <RuntimeSettings tab={panel} state={state} />
              ) : panel === "capabilities" ? (
                <section className="capability-page"><h3>能力</h3><Capabilities state={state} /></section>
              ) : panel === "requests" ? (
                <NativeRequests state={state} other />
              ) : panel === "help" ? (
                <div className="command-help capability-page">
                  <h3>快捷命令</h3>
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
        </DialogContent>
      </Dialog>
      <Dialog open={!!sessionPanel} onOpenChange={open => { if (!open) setSessionPanel(""); }}>
        <DialogContent className="settings-page session-page" showCloseButton={false} aria-describedby={undefined}
          onOpenAutoFocus={event => { event.preventDefault(); (event.target as HTMLElement).focus(); }}
          onCloseAutoFocus={event => { event.preventDefault(); document.querySelector<HTMLTextAreaElement>("#text")?.focus(); }}>
          <header className="settings-page-header"><div><DialogTitle>{sessionPanelNames[sessionPanel]}</DialogTitle><p className="session-scope-label">{session ? `当前聊天 · ${store.title(session.id)}` : "新聊天"}</p></div><TooltipIconButton tooltip="返回聊天" onClick={() => setSessionPanel("")}><X /></TooltipIconButton></header>
          <div className="settings-body" key={`${state.active}:${sessionPanel}`}><SessionPanel tab={sessionPanel} state={state} /></div>
        </DialogContent>
      </Dialog>
      <ChatSearch open={searchOpen} onOpenChange={setSearchOpen} state={state} />
      {reviewOpen && <ReviewPicker key={state.active || "new"} state={state} onClose={() => setReviewOpen(false)} />}
    </AssistantRuntimeProvider>
  );
}
createRoot(document.getElementById("root")!).render(
  <ResizableSidebarProvider>
    <Workspace />
  </ResizableSidebarProvider>,
);
