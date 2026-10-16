import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  SimpleImageAttachmentAdapter,
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
import { Thread } from "./components/assistant-ui/elements/thread.aui";
import { ThreadListSidebar } from "./components/assistant-ui/elements/threadlist-sidebar.aui";
import { TooltipIconButton } from "./components/assistant-ui/elements/tooltip-icon-button";
import {
  SidebarProvider,
  SidebarInset,
  SidebarTrigger,
  useSidebar,
} from "./components/ui/sidebar";
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
  useNativeRequestPolling,
} from "./native-ui";
import { native } from "./native";
import { CodexToolFallback } from "./tool-ui";
import { consoleStore as store, busyStatuses, statusLabel } from "./store";
import { toConversationMessages } from "./messages";
import { WorkspaceFilePreview } from "./workspace-file";
import "./style.css";

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
  { id: "history", label: "会话与 Agent", icon: History },
  { id: "skills", label: "Skills", icon: BookOpen },
  { id: "mcp", label: "MCP", icon: Wrench },
  { id: "apps", label: "Apps", icon: Puzzle },
  { id: "plugins", label: "插件", icon: Puzzle },
  { id: "terminal", label: "终端", icon: Terminal },
  { id: "files", label: "文件", icon: Folder },
  { id: "help", label: "快捷命令", icon: CircleHelp },
  { id: "capabilities", label: "能力", icon: Wrench },
  { id: "loop", label: "执行流程", icon: Workflow },
  { id: "native", label: "原生事件", icon: Code2 },
  { id: "protocol", label: "开发者工具", icon: Code2 },
];
const threadComponents = { ToolFallback: CodexToolFallback };
const attachments = new SimpleImageAttachmentAdapter();

function Workspace() {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const { isMobile, setOpenMobile, state: sidebarState } = useSidebar();
  const [panel, setPanel] = useState("");
  useNativeRequestPolling();
  const [theme, setTheme] = useState(
    localStorage.getItem("galatea.theme") || "light",
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
          custom: { status: s.status, cwd: s.cwd || s.settings?.cwd },
        })),
    [state.sessions, state.titles],
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
      state.creating ||
      state.settingsBusy ||
      (!!session && !["ready", ...busyStatuses].includes(session.status)),
    isLoading: state.loading,
    suggestions: messages.length ? [] : suggestions,
    onNew: async (message) => {
      await store.send(
        message.content
          .filter((p) => p.type === "text")
          .map((p) => p.text)
          .join("\n"),
        [
          ...message.content,
          ...(message.attachments || []).flatMap((a) => a.content),
        ].flatMap((p) =>
          p.type === "image" ? [{ type: "image", url: p.image }] : [],
        ),
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
        isLoading: state.health === null && !state.error,
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
    localStorage.setItem("galatea.theme", theme);
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
    try {
      const method = (
        {
          compact: "thread/compact/start",
          fork: "thread/fork",
          review: "review/start",
          undo: "thread/rollback",
          archive: "thread/archive",
        } as Record<string, string>
      )[action];
      const result = await native(method, {
        threadId: session.thread_id,
        ...(action === "review"
          ? { target: { type: "uncommittedChanges" }, delivery: "inline" }
          : {}),
        ...(action === "undo" ? { numTurns: 1 } : {}),
      });
      await store.refreshSessions();
      if (result.session) await store.select(result.session.id);
      else if (action === "archive") await store.select(null);
      else store.schedule();
    } catch (error) {
      store.fail(error);
    }
  };
  useEffect(() => {
    const panel = (event: Event) => {
      const name = (event as CustomEvent).detail;
      setPanel(
        (
          {
            model: "settings",
            permissions: "settings",
            plan: "settings",
            status: "loop",
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
  }, [session]);
  useEffect(() => {
    if (!state.nativeRequests.length) return;
    const title = document.title;
    document.title = `(${state.nativeRequests.length}) 需要回应 · Codex`;
    return () => {
      document.title = title;
    };
  }, [state.nativeRequests.length]);
  const connected = state.health?.host_ready && !state.streamError;
  const otherRequests = state.nativeRequests.filter(
    (r) => r.session_id !== state.active || !state.active,
  );
  const panelTitle =
    panel === "requests"
      ? "等待你的回应"
      : settingsSections.find((s) => s.id === panel)?.label || "设置";
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <WorkspaceFilePreview />
      <ThreadListSidebar
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
                <strong>
                  {(session?.cwd || state.nativeProtocol?.cwd || "本地工作区")
                    .split("/")
                    .filter(Boolean)
                    .at(-1)}
                </strong>
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
          <span
            className="chat-header-brand"
            title={session?.cwd || session?.settings?.cwd || ""}
          >
            {(session?.cwd || session?.settings?.cwd || "")
              .split("/")
              .filter(Boolean)
              .at(-1) || "Codex"}
          </span>
          <span
            id="session-title"
            title={state.active || ""}
            className="min-w-0 flex-1 truncate text-sm text-muted-foreground"
          >
            {state.global
              ? "运行实例事件"
              : state.active
                ? store.title(state.active)
                : ""}
          </span>
          <TooltipIconButton tooltip="打开文件" onClick={() => openSettings("files")}>
            <Folder />
          </TooltipIconButton>
          <TooltipIconButton tooltip="打开终端" onClick={() => openSettings("terminal")}>
            <Terminal />
          </TooltipIconButton>
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
                  review: "审查未提交修改",
                  undo: "回退上一轮",
                  archive: "归档会话",
                }).map(([action, label]) => (
                  <DropdownMenuItem
                    key={action}
                    disabled={session?.status !== "ready"}
                    onSelect={() => void nativeAction(action)}
                  >
                    {label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </header>
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
          {session && !["ready", ...busyStatuses].includes(session.status) && (
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
            <Thread
              components={threadComponents}
              interactions={<NativeRequests state={state} />}
            />
          </div>
        )}
      </SidebarInset>
      <Dialog
        open={!!panel}
        onOpenChange={(open) => {
          if (!open) setPanel("");
        }}
      >
        <DialogContent className="settings-dialog" showCloseButton={false}>
          <DialogHeader className="settings-heading">
            <div>
              <DialogTitle>{panelTitle}</DialogTitle>
              <DialogDescription className="sr-only">
                Codex 设置与会话工具
              </DialogDescription>
            </div>
            <DialogClose asChild>
              <TooltipIconButton tooltip="关闭详情">
                <X />
              </TooltipIconButton>
            </DialogClose>
          </DialogHeader>
          <div className="settings-layout">
            <nav className="settings-navigation" aria-label="设置分类">
              {settingsSections.map(({ id, label, icon: Icon }) => (
                <button
                  type="button"
                  key={id}
                  data-active={panel === id}
                  onClick={() => setPanel(id)}
                >
                  <Icon />
                  {label}
                </button>
              ))}
            </nav>
            <div className="settings-body" key={panel}>
              {panel === "capabilities" ? (
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
                  <p>输入 / 选择命令，使用 ↑ ↓ 选择，Enter 执行。</p>
                  <dl>
                    {[
                      ["/new", "新建会话"],
                      ["/model", "模型与推理强度"],
                      ["/plan", "切换计划模式"],
                      ["/permissions", "文件与命令权限"],
                      ["/skills", "浏览 Skills"],
                      ["/resume", "恢复会话"],
                      ["/review", "审查修改"],
                      ["/compact", "压缩上下文"],
                      ["/fork", "分叉会话"],
                    ].map(([key, label]) => (
                      <div key={key}>
                        <dt>{key}</dt>
                        <dd>{label}</dd>
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
    </AssistantRuntimeProvider>
  );
}
createRoot(document.getElementById("root")!).render(
  <SidebarProvider className="h-dvh overflow-hidden">
    <Workspace />
  </SidebarProvider>,
);
