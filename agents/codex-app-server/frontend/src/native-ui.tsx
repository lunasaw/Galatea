import { deferred } from "./deferred";
const AppsPanel = deferred(() => import("./apps-panel").then(m => ({ default: m.AppsPanel })));
const ModelSettings = deferred(() => import("./model-settings").then(m => ({ default: m.ModelSettings })));
const TerminalWorkspace = deferred(() => import("./terminal-workspace").then(m => ({ default: m.TerminalWorkspace })));
const SkillsPanel = deferred(() => import("./integrations").then(m => ({ default: m.SkillsPanel })));
const McpPanel = deferred(() => import("./integrations").then(m => ({ default: m.McpPanel })));
const PluginsPanel = deferred(() => import("./integrations").then(m => ({ default: m.PluginsPanel })));


import { requestThreadAction } from "./thread-actions";

import { FileCapabilities } from "./file-capabilities";
import {
  useEffect,
  useState,
  useRef,
  useId,
  Children,
  cloneElement,
  isValidElement,
} from "react";
import { Button } from "./components/ui/button";
import {
  api,
  consoleStore as store,
  requestKey,
  type ConsoleState,
} from "./store";
import {
  decode64,
  encode64,
  json,
  native,
  openNativeThread,
  sandboxPolicy,
  schemaValue,
  type Obj,
} from "./native";
import type { NativeRequest } from "./types";


const Json = ({ value }: { value: unknown }) => (
  <pre className="json">{json(value)}</pre>
);
const Field = ({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) => {
  const id = useId();
  return (
    <div className="native-field">
      <label htmlFor={id}>{label}</label>
      {Children.map(children, (child) =>
        isValidElement(child) &&
        ["input", "select", "textarea"].includes(child.type as string)
          ? cloneElement(child as React.ReactElement<{ id: string }>, { id })
          : child,
      )}
    </div>
  );
};
const ErrorText = ({ error }: { error: string }) =>
  error ? (
    <p role="alert" className="text-destructive whitespace-pre-wrap">
      {error}
    </p>
  ) : null;
const choices: Record<string, string> = {
  accept: "允许本次",
  acceptForSession: "允许此会话",
  decline: "拒绝",
  cancel: "取消",
  approved: "允许本次",
  approved_for_session: "允许此会话",
  denied: "拒绝",
  abort: "取消",
};
const safeUrl = (value: unknown) =>
  typeof value === "string" && /^https?:\/\//i.test(value) ? value : undefined;

function Interaction({ row }: { row: NativeRequest }) {
  const p = row.params as Obj;
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [content, setContent] = useState<Obj>({});
  const [raw, setRaw] = useState("{}");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [scope, setScope] = useState("turn");
  const send = async (result: unknown, rpcError?: Obj) => {
    setBusy(true);
    setError("");
    try {
      await api(
        `/api/native/requests/${row.id}`,
        rpcError ? { error: rpcError } : { result },
        requestKey(),
      );
      await store.refreshNativeRequests();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const approval =
    /commandExecution\/requestApproval|fileChange\/requestApproval|^execCommandApproval$|^applyPatchApproval$/.test(
      row.method,
    );
  const legacy = !row.method.includes("/");
  const decisions =
    p.availableDecisions ||
    (legacy
      ? ["approved", "approved_for_session", "denied", "abort"]
      : ["accept", "acceptForSession", "decline", "cancel"]);
  const questions = row.method === "item/tool/requestUserInput";
  const permission = row.method === "item/permissions/requestApproval";
  const mcp = row.method === "mcpServer/elicitation/request";
  return (
    <article className="native-request" aria-label="等待回应的请求">
      <header>
        <strong>
          {approval
            ? "执行审批"
            : questions
              ? "需要你的回答"
              : permission
                ? "权限申请"
                : mcp
                  ? "MCP 请求"
                  : "客户端请求"}
        </strong>
        {p.serverName && (
          <span className="text-xs text-muted-foreground">{p.serverName}</span>
        )}
      </header>
      {(p.reason || p.message) && <p>{p.reason || p.message}</p>}
      {p.command && (
        <pre className="json">
          {Array.isArray(p.command) ? p.command.join(" ") : p.command}
        </pre>
      )}
      {(p.cwd || p.grantRoot) && (
        <p className="text-xs text-muted-foreground">
          目录：{p.cwd || p.grantRoot}
        </p>
      )}
      {questions &&
        (p.questions || []).map((q: Obj) => (
          <div
            key={q.id}
            className="native-field"
            role="group"
            aria-label={q.question}
          >
            <span>{q.question}</span>
            <div className="native-options">
              {(q.options || []).map((option: Obj) => (
                <Button
                  type="button"
                  key={option.label}
                  variant={
                    answers[q.id] === option.label ? "default" : "outline"
                  }
                  aria-pressed={answers[q.id] === option.label}
                  aria-label={option.label}
                  className="question-choice"
                  onClick={() =>
                    setAnswers({ ...answers, [q.id]: option.label })
                  }
                >
                  <span>{option.label}</span>
                  {option.description && <small>{option.description}</small>}
                </Button>
              ))}
            </div>
            <input
              aria-label={q.header || q.id}
              type={q.isSecret ? "password" : "text"}
              autoComplete="off"
              placeholder="输入回答"
              value={answers[q.id] || ""}
              onChange={(e) =>
                setAnswers({ ...answers, [q.id]: e.target.value })
              }
            />
          </div>
        ))}
      {permission && (
        <>
          <Json value={p.permissions} />
          <Field label="授权范围">
            <select value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="turn">本轮</option>
              <option value="session">此会话</option>
            </select>
          </Field>
        </>
      )}
      {mcp &&
        (p.mode === "url" ? (
          safeUrl(p.url) && (
            <a target="_blank" rel="noreferrer" href={safeUrl(p.url)}>
              打开授权页面
            </a>
          )
        ) : (
          <>
            {Object.entries(p.requestedSchema?.properties || {}).map(
              ([key, value]) => {
                const schema = value as Obj;
                return (
                  <Field key={key} label={schema.title || key}>
                    {schema.enum ? (
                      <select
                        value={content[key] ?? ""}
                        onChange={(e) =>
                          setContent({ ...content, [key]: e.target.value })
                        }
                      >
                        <option value="">请选择</option>
                        {schema.enum.map((v: string) => (
                          <option key={v}>{v}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type={
                          schema.type === "boolean"
                            ? "checkbox"
                            : schema.type === "number" ||
                                schema.type === "integer"
                              ? "number"
                              : schema.format === "password"
                                ? "password"
                                : "text"
                        }
                        aria-label={key}
                        onChange={(e) =>
                          setContent({
                            ...content,
                            [key]:
                              schema.type === "boolean"
                                ? e.target.checked
                                : ["number", "integer"].includes(schema.type)
                                  ? Number(e.target.value)
                                  : e.target.value,
                          })
                        }
                      />
                    )}
                  </Field>
                );
              },
            )}
            <details>
              <summary>自定义表单 JSON</summary>
              <textarea
                aria-label="MCP 表单 JSON"
                value={json(content)}
                onChange={(e) => {
                  setRaw(e.target.value);
                  try {
                    setContent(JSON.parse(e.target.value));
                    setError("");
                  } catch {
                    setError("表单 JSON 无效");
                  }
                }}
              />
              <Json value={p.requestedSchema} />
            </details>
          </>
        ))}
      <fieldset disabled={busy} className="native-options">
        {approval &&
          decisions.map((decision: any, i: number) => (
            <Button
              key={i}
              variant={
                ["decline", "denied", "cancel", "abort"].includes(decision)
                  ? "outline"
                  : "default"
              }
              onClick={() =>
                void send({
                  decision:
                    legacy && decision === "denied"
                      ? { denied: { rejection: "User declined" } }
                      : decision,
                })
              }
            >
              {typeof decision === "string"
                ? choices[decision] || decision
                : "允许并保存规则：" + json(decision)}
            </Button>
          ))}
        {questions && (
          <Button
            disabled={(p.questions || []).some(
              (q: Obj) => !answers[q.id]?.trim(),
            )}
            onClick={() =>
              void send({
                answers: Object.fromEntries(
                  Object.entries(answers).map(([id, answer]) => [
                    id,
                    { answers: [answer] },
                  ]),
                ),
              })
            }
          >
            提交回答
          </Button>
        )}
        {permission && (
          <>
            <Button
              onClick={() => void send({ permissions: p.permissions, scope })}
            >
              授予所列权限
            </Button>
            <Button
              variant="outline"
              onClick={() => void send({ permissions: {}, scope: "turn" })}
            >
              拒绝
            </Button>
          </>
        )}
        {mcp &&
          ["accept", "decline", "cancel"].map((action) => (
            <Button
              key={action}
              variant={action === "accept" ? "default" : "outline"}
              onClick={() =>
                void send({
                  action,
                  content:
                    action === "accept" && p.mode !== "url" ? content : null,
                  _meta: null,
                })
              }
            >
              {action === "accept" ? "确认提交" : choices[action]}
            </Button>
          ))}
        {!(approval || permission || questions || mcp) && (
          <Button
            variant="outline"
            onClick={() =>
              void send(null, {
                code: -32000,
                message:
                  "Client cannot provide requested credentials or attestation",
              })
            }
          >
            无法提供，返回错误
          </Button>
        )}
      </fieldset>
      <details>
        <summary>请求详情与高级回应</summary>
        <Json value={p} />
        <textarea
          aria-label="原生请求回应 JSON"
          value={raw}
          onChange={(e) => setRaw(e.target.value)}
        />
        <Button
          disabled={busy}
          onClick={() => {
            try {
              void send(JSON.parse(raw));
            } catch (e) {
              setError(String(e));
            }
          }}
        >
          提交 JSON 回应
        </Button>
      </details>
      <ErrorText error={error} />
    </article>
  );
}

export function useNativeRequestPolling() {
  useEffect(() => {
    let stopped = false;
    const poll = async () => {
      await store.refreshNativeRequests();
      if (!stopped) timer = setTimeout(poll, 1000);
    };
    let timer: ReturnType<typeof setTimeout>;
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, []);
}

export function NativeRequests({
  state,
  other = false,
}: {
  state: ConsoleState;
  other?: boolean;
}) {
  const rows = state.nativeRequests.filter((row) =>
    other
      ? row.session_id !== state.active || !state.active
      : !!state.active && row.session_id === state.active,
  );
  return rows.length ? (
    <div className="native-requests" aria-live="polite">
      {rows.map((row) => (
        <div key={row.id}>
          {other && (
            <p className="request-thread">
              {row.session_id
                ? store.title(row.session_id)
                : `Agent · ${row.params.threadId || "运行实例"}`}
              {row.session_id && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    void store.select(row.session_id!);
                  }}
                >
                  打开会话
                </Button>
              )}
            </p>
          )}
          <Interaction row={row} />
        </div>
      ))}
    </div>
  ) : null;
}

export function ConversationHistory({ state, agents = false, onOpen }: { state: ConsoleState; agents?: boolean; onOpen?: () => void }) {
  const [data, setData] = useState<Obj>({});
  const [term, setTerm] = useState("");
  const [archived, setArchived] = useState(false);
  const threadId = state.sessions.find((s) => s.id === state.active)?.thread_id;
  const generation = useRef(0);
  const [error, setError] = useState("");
  const load = async (cursor?: string) => {
    const version = ++generation.current;
    if (agents && !threadId) { setData({ data: [] }); return; }
    try {
      const result = await native("thread/list", {
          limit: 50,
          searchTerm: term,
          archived,
          ...(cursor ? { cursor } : {}),
          ...(agents &&
          state.sessions.find((s) => s.id === state.active)?.thread_id
            ? {
                ancestorThreadId: state.sessions.find(
                  (s) => s.id === state.active,
                )!.thread_id,
                sourceKinds: [
                  "subAgent",
                  "subAgentReview",
                  "subAgentCompact",
                  "subAgentThreadSpawn",
                  "subAgentOther",
                ],
              }
            : {}),
        });
      if (version !== generation.current) return;
      setData((previous) => cursor ? { ...result, data: [...(previous.data || []), ...result.data] } : result);
      setError("");
    } catch (e) {
      if (version === generation.current) setError(String(e));
    }
  };
  useEffect(() => {
    void load();
    return () => { generation.current++; };
  }, [archived, agents, threadId]);
  useEffect(() => {
    const listener = () => void load();
    window.addEventListener("codex-threads-changed", listener);
    return () => window.removeEventListener("codex-threads-changed", listener);
  }, [archived, agents, term, threadId]);
  return (
    <div className="native-form">
      {agents && <><h3>当前聊天的子 Agent</h3>{!threadId && <p>先打开一个聊天。</p>}</>}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void load();
        }}
      >
        <Field label="搜索原生会话">
          <input value={term} onChange={(e) => setTerm(e.target.value)} />
        </Field>
        <Button>搜索</Button>
      </form>
      {!agents && <label className="native-checkbox">
        <input
          type="checkbox"
          checked={archived}
          onChange={(e) => setArchived(e.target.checked)}
        />
        已归档
      </label>}
      {(data.data || []).map((thread: Obj) => (
        <article className="native-card" key={thread.id}>
          <strong>{thread.name || thread.preview || thread.id}</strong>
          <code>{thread.cwd}</code>
          <div className="native-options">
            <Button
              onClick={async () => {
                try {
                  if (archived)
                    await native("thread/unarchive", { threadId: thread.id });
                  await openNativeThread(thread.id);
                  onOpen?.();
                } catch (e) {
                  setError(String(e));
                }
              }}
            >
              打开会话
            </Button>
            <Button
              variant="outline"
              onClick={() =>
                requestThreadAction({
                  action: archived ? "unarchive" : "archive",
                  threadId: thread.id,
                  title: thread.name || thread.preview,
                })
              }
            >
              {archived ? "取消归档" : "归档"}
            </Button>
            <Button
              variant="outline"
              onClick={() =>
                requestThreadAction({
                  action: "fork",
                  threadId: thread.id,
                  title: thread.name || thread.preview,
                })
              }
            >
              分叉会话
            </Button>
            <Button
              variant="outline"
              onClick={() =>
                requestThreadAction({
                  action: "delete",
                  threadId: thread.id,
                  title: thread.name || thread.preview,
                })
              }
            >
              删除会话
            </Button>
          </div>
        </article>
      ))}
      {data.nextCursor && (
        <Button variant="outline" onClick={() => void load(data.nextCursor)}>
          加载更多
        </Button>
      )}
      <ErrorText error={error} />
    </div>
  );
}

function ProtocolExplorer({ state }: { state: ConsoleState }) {
  const protocol = state.nativeProtocol as Obj | null;
  const [method, setMethod] = useState("thread/list");
  const [filter, setFilter] = useState("");
  const [params, setParams] = useState("{}");
  const [result, setResult] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const methods = (protocol?.methods || []).filter(
    (m: string) =>
      !/^account\//.test(m) &&
      !/oauth\/login/.test(m) &&
      !m.startsWith("userVerification/") &&
      m !== "attestation/generate",
  );
  const select = (name: string) => {
    setMethod(name);
    const value = schemaValue(
      protocol?.params[name] || {},
      protocol?.definitions || {},
    ) as Obj;
    if (value && "threadId" in value)
      value.threadId =
        state.sessions.find((s) => s.id === state.active)?.thread_id || "";
    setParams(json(value));
  };
  return (
    <div className="native-form">
      <p>
        {protocol?.version} · {methods.length}{" "}
        个可用原生方法。此工具供高级调试使用，账号认证接口不在网页中提供。
      </p>
      <Field label="筛选方法">
        <input value={filter} onChange={(e) => setFilter(e.target.value)} />
      </Field>
      <Field label="原生方法">
        <select value={method} onChange={(e) => select(e.target.value)}>
          {methods
            .filter(
              (m: string) =>
                !filter || m.toLowerCase().includes(filter.toLowerCase()),
            )
            .map((m: string) => (
              <option key={m}>{m}</option>
            ))}
        </select>
      </Field>
      <Field label="原生方法参数 JSON">
        <textarea
          className="native-editor"
          value={params}
          onChange={(e) => setParams(e.target.value)}
          spellCheck={false}
        />
      </Field>
      <Button
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError("");
          try {
            const r = await native(method, JSON.parse(params));
            setResult(r);
            await store.refreshSessions();
            if (r.session) await store.select(r.session.id);
          } catch (e) {
            setError(String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        执行所选方法
      </Button>
      <ErrorText error={error} />
      {result !== undefined && <Json value={result} />}
      <details>
        <summary>参数定义</summary>
        <Json
          value={
            protocol?.definitions?.[
              protocol?.params[method]?.$ref?.split("/").at(-1)
            ] || protocol?.params[method]
          }
        />
      </details>
    </div>
  );
}

export function NativeControl({
  state,
  initialTab = "settings",
  compact = false,
}: {
  state: ConsoleState;
  initialTab?: string;
  compact?: boolean;
}) {
  const tab = initialTab;
  return (
    <div id={compact ? "terminal-control" : "native-control"}>
      {tab === "settings" ? (
        <ModelSettings key={state.active || "new"} state={state} />
      ) : tab === "history" ? (
        <ConversationHistory state={state} />
      ) : tab === "terminal" ? (
        <TerminalWorkspace compact={compact} />
      ) : tab === "files" ? (
        <FileCapabilities />
      ) : tab === "protocol" ? (
        <ProtocolExplorer state={state} />
      ) : tab === "skills" ? (
        <SkillsPanel state={state} />
      ) : tab === "mcp" ? (
        <McpPanel state={state} />
      ) : tab === "plugins" ? (
        <PluginsPanel state={state} />
      ) : (
        <AppsPanel key={state.active || "global"} state={state} />
      )}
    </div>
  );
}
