import { deferred } from "./deferred";
const McpAppView = deferred(() => import("./mcp-app-view").then(m => ({ default: m.McpAppView })));
import {
  useAuiState,
  type ToolCallMessagePartProps,
} from "@assistant-ui/react";
import { GuardianReview } from "./review-capabilities";
import { NativeAgentActivity, NativeHookActivity, NativeImageActivity } from "./native-item-details";
import {
  Terminal,
  FileDiff,
  Wrench,
  Search,
  ListChecks,
  Check,
  Circle,
  LoaderCircle,
  AlertCircle,
  ChevronRight,
  Users,
  Layers,
  BookOpen,
  Folder,
} from "lucide-react";
import { ToolFallback } from "./components/assistant-ui/elements/tool-fallback.aui";
import { toolState } from "./messages";
import type { Step } from "./types";
import { WorkspaceFileScope, WorkspaceLink } from "./workspace-file";
import { commandActions } from "./command-actions";
import { McpContent } from "./mcp-capabilities";

import { consoleStore as store } from "./store";

const object = (v: unknown): Record<string, any> =>
  v && typeof v === "object" ? (v as Record<string, any>) : {};
const pretty = (v: unknown) =>
  typeof v === "string" ? v : JSON.stringify(v, null, 2);

export function CodexToolFallback(props: ToolCallMessagePartProps) {
  const meta = useAuiState((s) => s.message.metadata.custom);
  const step =
    (meta?.steps as Record<string, Step> | undefined)?.[props.toolCallId] ||
    (meta?.step as Step | undefined);
  if (!step) return <ToolFallback {...props} />;
  return <NativeToolActivity step={step} running={props.status.type === "running"} result={props.result} turnStatus={(meta?.turn as { status?: string } | undefined)?.status} />;
}

/** The same native activity presentation is used in the main chat and agent drawer. */
export function NativeToolActivity({ step, running = false, result, threadId: scopeThreadId, turnStatus }: { step: Step; running?: boolean; result?: unknown; threadId?: string; turnStatus?: string }) {
  const d = object(step.detail);
  if (step.type === "guardian") return <GuardianReview notification={d} />;
  if (["imageView", "imageGeneration"].includes(step.type || "")) return <NativeImageActivity step={step} />;
  if (["subAgentActivity", "collabAgentToolCall"].includes(step.type || "")) return <NativeAgentActivity step={step} />;
  if (step.type === "hook") return <NativeHookActivity step={step} />;
  if (step.type === "mcpToolCall") {
    const uri = d.mcpAppUi?.resourceUri || d.appContext?.resourceUri || d.mcpAppResourceUri;
    const threadId = scopeThreadId || store.state.sessions.find((s) => s.id === store.state.active)?.thread_id;
    const status = d.status === "inProgress" && ["interrupted", "failed"].includes(turnStatus || "") ? turnStatus : d.status;
    return <section className="native-rich-item"><strong>{d.appContext?.appName || d.server} · {d.appContext?.actionName || d.tool}</strong>
      {d.status === "inProgress" && <p role="status">执行中…</p>}
      {d.error && <p role="alert">{d.error.message || pretty(d.error)}</p>}
      {d.result && <McpContent content={d.result.content || []} />}
      {typeof uri === "string" && uri.startsWith("ui://") && threadId && <McpAppView server={d.server} threadId={threadId} uri={uri} instanceId={step.id} toolName={d.tool} args={d.arguments} result={d.result} status={status} toolError={d.error?.message} />}
      <details className="native-arguments"><summary>调用详情</summary><pre className="json">{pretty({ arguments: d.arguments, result: d.result })}</pre></details>
    </section>;
  }
  const state = toolState(step);
  const pending = state === "running" && !running;
  const actions = step.type === "commandExecution" ? commandActions(d) : [];
  const label =
    step.type === "warning"
      ? "提示"
      : step.type === "turn/plan/updated"
        ? `${(d.plan || []).filter((item: any) => item.status === "completed").length}/${(d.plan || []).length}`
        : pending
          ? "未收到完成回执"
          : {
              running: "执行中",
              complete: "已完成",
              failed: "失败",
              unknown: "结果未知",
            }[state];
  const Icon =
    step.type === "commandExecution"
      ? actions[0]?.type === "read"
        ? BookOpen
        : actions[0]?.type === "search"
          ? Search
          : actions[0]?.type === "listFiles"
            ? Folder
            : Terminal
      : step.type === "fileChange"
        ? FileDiff
        : step.type === "webSearch"
          ? Search
          : step.type.includes("plan") || step.type === "plan"
            ? ListChecks
            : step.type === "collabAgentToolCall"
              ? Users
              : step.type === "contextCompaction"
                ? Layers
                : Wrench;
  const title =
    step.type === "commandExecution"
      ? d.command || "运行命令"
      : step.type === "fileChange"
        ? `修改 ${(d.changes || []).length} 个文件`
        : step.type === "turn/plan/updated"
          ? "更新计划"
          : step.type === "plan"
            ? "执行计划"
            : step.type === "webSearch"
              ? `搜索 ${d.query || "网页"}`
              : step.type === "contextCompaction"
                ? "已压缩会话上下文"
                : step.type === "warning"
                  ? "运行提示"
                  : step.type === "error"
                    ? "执行错误"
                    : d.tool || step.tool || step.type;
  const output = d.aggregatedOutput ?? step.text;
  return (
    <details
      className="native-activity"
      data-slot="tool-fallback-root"
      data-state={state}
    >
      <summary data-slot="tool-fallback-trigger">
        <Icon size={15} aria-hidden />
        <span className="native-activity-title" title={title}>
          {actions.length ? (
            <WorkspaceFileScope cwd={typeof d.cwd === "string" ? d.cwd : ""}>
              {actions.map((action, index) => (
                <span key={index}>
                  {index > 0 ? " · " : ""}
                  {action.type === "read" ? (
                    <>
                      {state === "complete"
                        ? "已读取 "
                        : state === "running" && !pending
                          ? "正在读取 "
                          : "读取 "}
                      {action.path ? (
                        <WorkspaceLink href={action.path}>
                          {action.name || action.path.split("/").at(-1)}
                        </WorkspaceLink>
                      ) : (
                        action.name || "文件"
                      )}
                    </>
                  ) : action.type === "search" ? (
                    <>
                      {state === "complete" ? "已在" : "在"}{" "}
                      {action.path || "工作目录"} 搜索
                      {action.query ? ` “${action.query}”` : "文件"}
                    </>
                  ) : (
                    <>
                      {state === "complete" ? "已浏览" : "浏览"}{" "}
                      {action.path || "工作目录"}
                    </>
                  )}
                </span>
              ))}
            </WorkspaceFileScope>
          ) : (
            title
          )}
        </span>
        <span
          className="native-activity-status"
          data-complete={state === "complete"}
        >
          {state === "running" && !pending ? (
            <LoaderCircle className="animate-spin" size={12} />
          ) : state === "failed" ? (
            <AlertCircle size={12} />
          ) : null}
          {label}
        </span>
        <ChevronRight size={13} className="native-activity-chevron" />
      </summary>
      <div className="native-activity-body">
        {step.type === "commandExecution" ? (
          <>
            <div className="native-command-meta">
              <span>{d.cwd}</span>
              <span>
                {typeof d.exitCode === "number" ? `退出码 ${d.exitCode}` : ""}
                {typeof d.durationMs === "number"
                  ? ` · ${(d.durationMs / 1000).toFixed(1)} 秒`
                  : ""}
              </span>
            </div>
            <pre className="native-terminal">
              $ {d.command}
              {output ? `\n\n${output}` : ""}
            </pre>
          </>
        ) : step.type === "fileChange" ? (
          (d.changes || []).map((change: any, i: number) => (
            <div key={i} className="native-file-change">
              <WorkspaceLink href={change.path}>{change.path}</WorkspaceLink>
              <pre className="native-diff">
                {String(change.diff || "")
                  .split("\n")
                  .map((line, n) => (
                    <span
                      key={n}
                      data-change={
                        line.startsWith("+")
                          ? "add"
                          : line.startsWith("-")
                            ? "remove"
                            : "context"
                      }
                    >
                      {line}
                      {"\n"}
                    </span>
                  ))}
              </pre>
            </div>
          ))
        ) : step.type === "turn/plan/updated" ? (
          <>
            {d.explanation && <p>{d.explanation}</p>}
            <ol className="native-plan">
              {(d.plan || []).map((item: any, i: number) => (
                <li key={i} data-status={item.status}>
                  {item.status === "completed" ? (
                    <Check size={14} />
                  ) : item.status === "inProgress" ? (
                    <LoaderCircle size={14} className="animate-spin" />
                  ) : (
                    <Circle size={14} />
                  )}
                  <span>{item.step}</span>
                </li>
              ))}
            </ol>
          </>
        ) : step.type === "plan" ? (
          <pre className="native-plan-text">{d.text || step.text}</pre>
        ) : step.type === "contextCompaction" ? (
          <p>较早的上下文已压缩，原始会话记录仍可查看。</p>
        ) : (
          <>
            {step.type === "warning" || step.type === "error" ? (
              <p role="status">
                {d.message || object(d.error).message || pretty(d)}
              </p>
            ) : (
              <>
                <details className="native-arguments">
                  <summary>调用参数</summary>
                  <pre className="json">
                    {pretty(step.arguments ?? d.arguments ?? d)}
                  </pre>
                </details>
                {output && <pre className="native-terminal">{output}</pre>}
                {(result !== undefined ||
                  d.result !== undefined ||
                  d.error) && (
                  <div>
                    <p className="muted">工具回执</p>
                    <pre className="json">
                      {pretty(result ?? d.result ?? d.error)}
                    </pre>
                  </div>
                )}
              </>
            )}
          </>
        )}
      </div>
    </details>
  );
}
