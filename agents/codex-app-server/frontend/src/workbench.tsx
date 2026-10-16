import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import {
  Check,
  ChevronDown,
  ChevronRight,
  File,
  FileDiff,
  Folder,
  Info,
  ListChecks,
  LoaderCircle,
  RefreshCw,
  Terminal,
  WrapText,
  X,
  ArrowUp,
} from "lucide-react";
import { TooltipIconButton } from "./components/assistant-ui/elements/tooltip-icon-button";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "./components/ui/dialog";
import { NativeControl } from "./native-ui";
import { native } from "./native";
import { openFile, WorkspaceFileView, type FileTarget } from "./workspace-file";
import { statusLabel, type ConsoleState } from "./store";
import { reviewFiles, reviewTurns } from "./workbench-data";

export type WorkbenchTab =
  | "launcher"
  | "review"
  | "files"
  | "summary"
  | "preview";
const titles = {
  launcher: "新标签页",
  review: "更改",
  files: "文件",
  summary: "摘要",
  preview: "文件预览",
};
const object = (value: unknown): Record<string, any> =>
  value && typeof value === "object" ? (value as Record<string, any>) : {};

function revealSelectedTab(tabs: HTMLDivElement | null) {
  const selected = tabs?.querySelector<HTMLElement>('[aria-selected="true"]');
  if (!tabs || !selected) return;
  const item = selected.getBoundingClientRect();
  const bounds = tabs.getBoundingClientRect();
  if (item.right > bounds.right)
    tabs.scrollLeft += item.right - bounds.right + 24;
  else if (item.left < bounds.left) tabs.scrollLeft -= bounds.left - item.left;
}

function Review({ state }: { state: ConsoleState }) {
  const turns = reviewTurns(state.snapshot);
  const [selected, setSelected] = useState("");
  const [wrap, setWrap] = useState(true);
  const turnId = turns.some((turn) => turn.id === selected)
    ? selected
    : turns.at(-1)?.id;
  const files = reviewFiles(state.snapshot, turnId);
  return (
    <>
      <div className="review-toolbar">
        <label className="sr-only" htmlFor="review-turn">
          查看轮次
        </label>
        <select
          id="review-turn"
          value={turnId || ""}
          onChange={(event) => setSelected(event.target.value)}
          disabled={!turns.length}
        >
          {!turns.length && <option value="">暂无更改</option>}
          {turns.map((turn, index) => (
            <option key={turn.id} value={turn.id}>
              第 {index + 1} 轮更改
            </option>
          ))}
        </select>
        <span>{files.length} 个文件</span>
        <TooltipIconButton
          tooltip="自动换行"
          aria-pressed={wrap}
          onClick={() => setWrap(!wrap)}
        >
          <WrapText />
        </TooltipIconButton>
      </div>
      {files.length ? (
        <div className="review-files" data-wrap={wrap}>
          {files.map((file) => (
            <details className="review-file" key={file.path} open>
              <summary>
                <ChevronDown size={14} />
                <span title={file.path}>{file.path.split("/").at(-1)}</span>
                <span className="diff-count">
                  <b>+{file.additions}</b>
                  <em>−{file.removals}</em>
                </span>
              </summary>
              <button
                className="review-file-path"
                onClick={() =>
                  openFile(
                    file.path,
                    state.sessions.find(
                      (session) => session.id === state.active,
                    )?.cwd ||
                      state.nativeProtocol?.cwd ||
                      "",
                  )
                }
              >
                {file.path}
              </button>
              <pre className="review-diff">
                {file.diff.split("\n").map((line, index) => (
                  <span
                    key={index}
                    data-change={
                      line.startsWith("+++") || line.startsWith("---")
                        ? "meta"
                        : line.startsWith("+")
                          ? "add"
                          : line.startsWith("-")
                            ? "remove"
                            : line.startsWith("@@")
                              ? "hunk"
                              : "context"
                    }
                  >
                    {line || " "}
                    {"\n"}
                  </span>
                ))}
              </pre>
            </details>
          ))}
        </div>
      ) : (
        <div className="workbench-empty">
          <FileDiff />
          <strong>还没有文件更改</strong>
          <p>Codex 修改文件后，可以在这里按轮次查看差异。</p>
        </div>
      )}
      {state.snapshot?.coverage.status !== "complete" && files.length > 0 && (
        <p className="workbench-note">
          显示已加载记录中的更改；可在对话中加载更早消息。
        </p>
      )}
    </>
  );
}

function Files({ cwd }: { cwd: string }) {
  const [path, setPath] = useState(cwd);
  const [revision, setRevision] = useState(0);
  const [entries, setEntries] = useState<
    Array<{ fileName: string; isDirectory: boolean; isFile: boolean }>
  >([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!path) return;
    let current = true;
    setLoading(true);
    setError("");
    setEntries([]);
    native("fs/readDirectory", { path })
      .then((result) => {
        if (current)
          setEntries(
            (result.entries || [])
              .slice()
              .sort(
                (
                  a: { isDirectory: boolean; fileName: string },
                  b: { isDirectory: boolean; fileName: string },
                ) =>
                  Number(b.isDirectory) - Number(a.isDirectory) ||
                  a.fileName.localeCompare(b.fileName),
              ),
          );
      })
      .catch((error) => {
        if (current) setError(String(error));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [path, revision]);
  return (
    <>
      <div className="file-browser-path">
        <TooltipIconButton
          tooltip="上一级目录"
          disabled={!path || path === "/"}
          onClick={() =>
            setPath(
              path.replace(/\/+$/, "").split("/").slice(0, -1).join("/") || "/",
            )
          }
        >
          <ArrowUp />
        </TooltipIconButton>
        <span title={path}>{path || "未选择工作目录"}</span>
        <TooltipIconButton
          tooltip="刷新文件"
          disabled={loading || !path}
          onClick={() => setRevision(revision + 1)}
        >
          <RefreshCw />
        </TooltipIconButton>
      </div>
      {error && (
        <p role="alert" className="workbench-note">
          {error}
        </p>
      )}
      {loading ? (
        <div className="workbench-empty" role="status">
          <LoaderCircle className="animate-spin" />
          正在读取目录
        </div>
      ) : (
        <div className="file-browser-list">
          {entries.map((entry) => (
            <button
              key={entry.fileName}
              disabled={!entry.isDirectory && !entry.isFile}
              onClick={() => {
                const next = `${path.replace(/\/+$/, "")}/${entry.fileName}`;
                if (entry.isDirectory) setPath(next);
                else openFile(next);
              }}
            >
              {entry.isDirectory ? <Folder /> : <File />}
              <span>{entry.fileName}</span>
              {entry.isDirectory && <ChevronRight />}
            </button>
          ))}
          {!entries.length && !error && (
            <p className="workbench-note">
              {path ? "目录为空" : "请选择工作目录"}
            </p>
          )}
        </div>
      )}
    </>
  );
}

function Summary({ state }: { state: ConsoleState }) {
  const session = state.sessions.find((session) => session.id === state.active);
  const latestPlan = [...(state.snapshot?.steps || [])]
    .reverse()
    .find((step) => step.type === "turn/plan/updated");
  const plan = object(latestPlan?.detail).plan;
  const usage = object(
    [...(state.snapshot?.turns || [])].reverse().find((turn) => turn.usage)
      ?.usage,
  );
  const used = Number(object(usage.last).totalTokens);
  const limit = Number(usage.modelContextWindow);
  return (
    <div className="workbench-summary">
      <section>
        <h3>
          <Terminal />
          环境
        </h3>
        <dl>
          <div>
            <dt>工作目录</dt>
            <dd>{session?.cwd || state.nativeProtocol?.cwd || "未选择"}</dd>
          </div>
          <div>
            <dt>状态</dt>
            <dd>{session ? statusLabel(session.status) : "新会话"}</dd>
          </div>
          <div>
            <dt>模型</dt>
            <dd>
              {session?.settings?.model ||
                state.preferences.model ||
                "默认模型"}
            </dd>
          </div>
        </dl>
      </section>
      <section>
        <h3>
          <ListChecks />
          计划
        </h3>
        {Array.isArray(plan) && plan.length ? (
          <ol className="summary-plan">
            {plan.map((item, index) => (
              <li key={index} data-status={item.status}>
                {item.status === "completed" ? (
                  <Check />
                ) : item.status === "inProgress" ? (
                  <LoaderCircle className="animate-spin" />
                ) : (
                  <span className="plan-pending" />
                )}
                <span>{item.step}</span>
              </li>
            ))}
          </ol>
        ) : (
          <p>本次会话还没有执行计划。</p>
        )}
      </section>
      <section>
        <h3>
          <Info />
          上下文
        </h3>
        {Number.isFinite(used) && limit > 0 ? (
          <>
            <div className="context-meter">
              <span>{Math.round((used / limit) * 100)}% 已使用</span>
              <span>
                {used.toLocaleString()} / {limit.toLocaleString()}
              </span>
            </div>
            <progress value={used} max={limit} aria-label="上下文使用量" />
          </>
        ) : (
          <p>运行后显示模型报告的上下文用量。</p>
        )}
      </section>
    </div>
  );
}

export function WorkbenchPanel({
  tab,
  state,
  mobile,
  onTab,
  onClose,
}: {
  tab: WorkbenchTab | null;
  state: ConsoleState;
  mobile: boolean;
  onTab: (tab: WorkbenchTab) => void;
  onClose: () => void;
}) {
  const [files, setFiles] = useState<FileTarget[]>([]);
  const [narrow, setNarrow] = useState(
    () => window.matchMedia("(max-width: 1100px)").matches,
  );
  const [activeFile, setActiveFile] = useState("");
  const [visited, setVisited] = useState<Set<WorkbenchTab>>(new Set());
  const [width, setWidth] = useState(480);
  const resize = useRef<{ x: number; width: number } | null>(null);
  const previousSession = useRef(state.active);
  const tabsElement = useRef<HTMLDivElement>(null);
  const mountTabs = useCallback((node: HTMLDivElement | null) => {
    tabsElement.current = node;
    if (!node) return;
    const observer = new ResizeObserver(() => revealSelectedTab(node));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    revealSelectedTab(tabsElement.current);
  }, [tab, activeFile, files, narrow, mobile]);
  useEffect(() => {
    if (previousSession.current === state.active) return;
    previousSession.current = state.active;
    setFiles([]);
    setActiveFile("");
    setVisited(new Set());
    if (tab === "preview") onClose();
  }, [state.active, tab, onClose]);
  useEffect(() => {
    if (tab)
      setVisited((value) =>
        value.has(tab) ? value : new Set([...value, tab]),
      );
  }, [tab]);
  useEffect(() => {
    const open = (event: Event) => {
      const target = (event as CustomEvent<FileTarget>).detail;
      if (
        !target ||
        typeof target.path !== "string" ||
        !target.path.startsWith("/")
      )
        return;
      setFiles((value) =>
        value.some((file) => file.path === target.path)
          ? value.map((file) =>
              file.path === target.path ? { ...target } : file,
            )
          : [...value, { ...target }],
      );
      setActiveFile(target.path);
      onTab("preview");
    };
    window.addEventListener("codex-file", open);
    return () => window.removeEventListener("codex-file", open);
  }, [onTab]);
  const closeFile = (path: string) => {
    const remaining = files.filter((file) => file.path !== path);
    setFiles(remaining);
    if (path === activeFile) {
      setActiveFile(remaining.at(-1)?.path || "");
      if (tab === "preview" && !remaining.length) onTab("files");
    }
  };
  const target = files.find((file) => file.path === activeFile);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 1100px)");
    const update = () => setNarrow(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  if (!tab) return null;
  const cwd =
    state.sessions.find((session) => session.id === state.active)?.cwd ||
    state.nativeProtocol?.cwd ||
    "";
  const content = (
    <>
      <div className="workbench-panel-header">
        <div
          ref={mountTabs}
          className="workbench-panel-tabs"
          role="tablist"
          aria-label="任务面板标签"
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
              return;
            const tabs = Array.from(
              event.currentTarget.querySelectorAll<HTMLButtonElement>(
                '[role="tab"]',
              ),
            );
            const current = tabs.indexOf(
              document.activeElement as HTMLButtonElement,
            );
            if (current < 0) return;
            const index =
              event.key === "Home"
                ? 0
                : event.key === "End"
                  ? tabs.length - 1
                  : (current +
                      (event.key === "ArrowRight" ? 1 : -1) +
                      tabs.length) %
                    tabs.length;
            event.preventDefault();
            tabs[index]?.focus();
            tabs[index]?.click();
          }}
        >
          {(["launcher", "review", "files", "summary"] as const).map((item) => (
            <button
              role="tab"
              key={item}
              aria-selected={tab === item}
              tabIndex={tab === item ? 0 : -1}
              onClick={() => onTab(item)}
            >
              {titles[item]}
            </button>
          ))}
          {files.map((file) => (
            <div className="workspace-file-tab" key={file.path}>
              <button
                role="tab"
                aria-selected={tab === "preview" && activeFile === file.path}
                tabIndex={
                  tab === "preview" && activeFile === file.path ? 0 : -1
                }
                title={file.path}
                onClick={() => {
                  setActiveFile(file.path);
                  onTab("preview");
                }}
              >
                <File size={13} />
                {file.path.split("/").at(-1)}
              </button>
              <button
                aria-label={`关闭 ${file.path.split("/").at(-1)}`}
                title={`关闭 ${file.path}`}
                onClick={() => closeFile(file.path)}
              >
                <X size={12} />
              </button>
            </div>
          ))}
        </div>
        <TooltipIconButton tooltip="关闭任务面板" onClick={onClose}>
          <X />
        </TooltipIconButton>
      </div>
      <div
        className="workbench-panel-content"
        role="tabpanel"
        aria-label={`${titles[tab]}面板`}
      >
        {tab === "launcher" ? (
          <div className="workbench-launcher">
            <p>会话工具</p>
            {(
              [
                {
                  id: "review",
                  title: "更改",
                  description: "检查本次会话的文件差异",
                  Icon: FileDiff,
                },
                {
                  id: "files",
                  title: "文件",
                  description: "浏览工作目录与预览文件",
                  Icon: Folder,
                },
                {
                  id: "summary",
                  title: "摘要",
                  description: "查看环境、计划和上下文",
                  Icon: Info,
                },
              ] as const
            ).map(({ id, title, description, Icon }) => (
              <button key={id} onClick={() => onTab(id)}>
                <Icon />
                <span>
                  <strong>{title}</strong>
                  <small>{description}</small>
                </span>
                <ChevronRight />
              </button>
            ))}
          </div>
        ) : null}
        {(visited.has("review") || tab === "review") && (
          <div hidden={tab !== "review"}>
            <Review key={state.active || "new"} state={state} />
          </div>
        )}
        {(visited.has("files") || tab === "files") && (
          <div hidden={tab !== "files"}>
            <Files key={cwd} cwd={cwd} />
          </div>
        )}
        {tab === "summary" && <Summary state={state} />}
        {files.map((file) => (
          <div
            className="workspace-file-page"
            hidden={tab !== "preview" || activeFile !== file.path}
            key={file.path}
          >
            <WorkspaceFileView
              target={file}
              active={tab === "preview" && activeFile === file.path}
            />
          </div>
        ))}
      </div>
    </>
  );
  return mobile || narrow ? (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="workbench-mobile-panel" showCloseButton={false}>
        <DialogTitle className="sr-only">
          {tab === "preview"
            ? target?.path.split("/").at(-1) || titles[tab]
            : titles[tab]}
        </DialogTitle>
        <DialogDescription className="sr-only">
          当前会话的文件、更改和摘要
        </DialogDescription>
        {content}
      </DialogContent>
    </Dialog>
  ) : (
    <aside
      className="workbench-panel"
      data-file-open={tab === "preview"}
      aria-label="任务面板"
      style={{ "--file-panel-width": `${width}px` } as CSSProperties}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <div
        className="workbench-resize"
        role="separator"
        aria-label="调整任务面板宽度"
        aria-orientation="vertical"
        tabIndex={0}
        aria-valuemin={320}
        aria-valuemax={Math.round(window.innerWidth * 0.6)}
        aria-valuenow={width}
        onKeyDown={(event) => {
          if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
            event.preventDefault();
            setWidth(
              Math.max(
                320,
                Math.min(
                  window.innerWidth * 0.6,
                  width + (event.key === "ArrowLeft" ? 32 : -32),
                ),
              ),
            );
          }
        }}
        onPointerDown={(event) => {
          resize.current = { x: event.clientX, width };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          if (resize.current)
            setWidth(
              Math.max(
                320,
                Math.min(
                  window.innerWidth * 0.6,
                  resize.current.width + resize.current.x - event.clientX,
                ),
              ),
            );
        }}
        onPointerUp={(event) => {
          resize.current = null;
          event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerCancel={() => {
          resize.current = null;
        }}
      />
      {content}
    </aside>
  );
}

export function BottomPanel({
  state,
  open,
  onClose,
}: {
  state: ConsoleState;
  open: boolean;
  onClose: () => void;
}) {
  return (
    <section className="workbench-bottom" aria-label="底部终端" hidden={!open}>
      <div className="workbench-panel-header">
        <span className="workbench-active-tab">
          <Terminal size={14} />
          终端
        </span>
        <TooltipIconButton tooltip="关闭终端" onClick={onClose}>
          <X />
        </TooltipIconButton>
      </div>
      <div className="workbench-terminal-body">
        <NativeControl state={state} initialTab="terminal" compact />
      </div>
    </section>
  );
}
