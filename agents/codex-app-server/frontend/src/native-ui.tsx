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

function Settings({ state }: { state: ConsoleState }) {
  const session = state.sessions.find((s) => s.id === state.active);
  const [models, setModels] = useState<Obj[]>([]);
  const currentSettings = store.composerSettings;
  const [model, setModel] = useState(currentSettings.model);
  const [effort, setEffort] = useState(currentSettings.effort);
  const [cwd, setCwd] = useState(
    session?.cwd || state.nativeProtocol?.cwd || "",
  );
  const policyType = session?.settings?.sandboxPolicy?.type;
  const [sandbox, setSandbox] = useState(
    (
      {
        readOnly: "read-only",
        workspaceWrite: "workspace-write",
        dangerFullAccess: "danger-full-access",
      } as Obj
    )[policyType] ||
      session?.settings?.sandbox ||
      "workspace-write",
  );
  const [approval, setApproval] = useState(
    session?.settings?.approvalPolicy || "on-request",
  );
  const [mode, setMode] = useState(currentSettings.mode);
  const [search, setSearch] = useState("live");
  const [flags, setFlags] = useState<Obj>({});
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    void Promise.all([
      native("model/list", {}),
      native("config/read", { includeLayers: false }),
    ])
      .then(([m, c]) => {
        if (!live) return;
        setModels(m.data || []);
        setModel(currentSettings.model || c.config?.model || "");
        setEffort(
          currentSettings.effort ||
            c.config?.model_reasoning_effort ||
            "medium",
        );
        setFlags(c.config?.features || {});
        setSearch(c.config?.web_search || "live");
      })
      .catch((e) => live && setError(String(e)));
    return () => {
      live = false;
    };
  }, []);
  const save = async (create: boolean) => {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      let id = session?.id;
      if (create || !session) {
        const result = await api<Obj>(
          "/api/sessions",
          {
            options: {
              cwd,
              ...(model ? { model } : {}),
              sandbox,
              approvalPolicy: approval,
            },
          },
          requestKey(),
        );
        id = result.id;
        await store.refreshSessions();
        await store.select(id!);
      }
      const current = store.state.sessions.find((s) => s.id === id)!;
      await native("thread/settings/update", {
        threadId: current.thread_id,
        cwd,
        ...(model ? { model } : {}),
        effort,
        sandboxPolicy: sandboxPolicy(sandbox),
        approvalPolicy: approval,
        ...(model
          ? {
              collaborationMode: {
                mode,
                settings: {
                  model,
                  reasoning_effort: effort,
                  developer_instructions: null,
                },
              },
            }
          : {}),
      });
      await store.refreshSessions();
      setStatus("会话设置已应用");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="native-form">
      <Field label="工作目录">
        <input
          value={cwd}
          onChange={(e) => setCwd(e.target.value)}
          placeholder="绝对路径"
        />
      </Field>
      <Field label="模型">
        <input
          aria-label="模型"
          list="native-models"
          value={model}
          onChange={(e) => setModel(e.target.value)}
        />
        <datalist id="native-models">
          {models.map((m) => (
            <option key={m.id} value={m.model}>
              {m.displayName}
            </option>
          ))}
        </datalist>
      </Field>
      <div className="native-grid">
        <Field label="推理强度">
          <select value={effort} onChange={(e) => setEffort(e.target.value)}>
            {[
              "none",
              "minimal",
              "low",
              "medium",
              "high",
              "xhigh",
              "max",
              "ultra",
            ].map((e) => (
              <option key={e}>{e}</option>
            ))}
          </select>
        </Field>
        <Field label="工作模式">
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="default">执行</option>
            <option value="plan">计划</option>
          </select>
        </Field>
      </div>
      <Field label="文件与命令权限">
        <select value={sandbox} onChange={(e) => setSandbox(e.target.value)}>
          <option value="read-only">只读</option>
          <option value="workspace-write">允许工作区写入</option>
          <option value="danger-full-access">完全访问</option>
        </select>
      </Field>
      <Field label="审批策略">
        <select value={approval} onChange={(e) => setApproval(e.target.value)}>
          <option value="untrusted">不可信操作需审批</option>
          <option value="on-request">按需请求审批</option>
          <option value="never">不请求审批</option>
        </select>
      </Field>
      <div className="native-options">
        <Button disabled={busy} onClick={() => void save(false)}>
          {session ? "应用到当前会话" : "创建会话"}
        </Button>
        {session && (
          <Button
            disabled={busy}
            variant="outline"
            onClick={() => void save(true)}
          >
            以此设置新建
          </Button>
        )}
      </div>
      <details>
        <summary>全局功能配置</summary>
        <p className="text-sm text-muted-foreground">
          修改后用于后续会话；部分功能取决于服务端和平台支持。
        </p>
        <Field label="联网搜索">
          <select value={search} onChange={(e) => setSearch(e.target.value)}>
            {["disabled", "cached", "live"].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </select>
        </Field>
        {Object.entries({
          shell_tool: "Shell 执行",
          unified_exec: "交互命令",
          view_image: "查看图片",
          multi_agent: "多 Agent",
          apps: "Apps",
          plugins: "Plugins",
          goals: "目标",
          hooks: "Hooks",
          skill_search: "Skill 搜索",
        }).map(([key, title]) => (
          <label className="native-checkbox" key={key}>
            <input
              type="checkbox"
              checked={flags[key] === true}
              onChange={(e) => setFlags({ ...flags, [key]: e.target.checked })}
            />
            {title}
          </label>
        ))}
        <Button
          disabled={busy || !state.nativeProtocol?.configuration_editable}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              await native("config/batchWrite", {
                reloadUserConfig: true,
                edits: [
                  {
                    keyPath: "web_search",
                    value: search,
                    mergeStrategy: "replace",
                  },
                  ...Object.entries(flags).map(([key, value]) => ({
                    keyPath: `features.${key}`,
                    value,
                    mergeStrategy: "replace",
                  })),
                ],
              });
              setStatus("全局配置已保存");
            } catch (e) {
              setError(String(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          保存全局功能
        </Button>
      </details>
      <p role="status">{status}</p>
      <ErrorText error={error} />
    </div>
  );
}

function Library({ kind, state }: { kind: string; state: ConsoleState }) {
  const [data, setData] = useState<Obj>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [value, setValue] = useState('{"command":"","args":[]}');
  const method = (
    {
      skills: "skills/list",
      mcp: "mcpServerStatus/list",
      plugins: "plugin/list",
      apps: "app/list",
    } as Obj
  )[kind];
  const load = async () => {
    setBusy(true);
    setError("");
    try {
      setData(
        await native(
          method,
          kind === "skills"
            ? {
                cwds: [
                  state.sessions.find((s) => s.id === state.active)?.cwd ||
                    state.nativeProtocol?.cwd,
                ].filter(Boolean),
                forceReload: true,
              }
            : {},
        ),
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    void load();
  }, [kind]);
  const act = async (method: string, params: Obj) => {
    setError("");
    try {
      const r = await native(method, params);
      if (safeUrl(r.authorizationUrl || r.authUrl || r.url)) setData(r);
      else await load();
    } catch (e) {
      setError(String(e));
    }
  };
  const skills: Obj[] = (data.data || []).flatMap(
    (entry: Obj) => entry.skills || [],
  );
  return (
    <div className="native-form">
      <div className="native-options">
        <Button variant="outline" disabled={busy} onClick={() => void load()}>
          刷新列表
        </Button>
      </div>
      {kind === "skills" &&
        skills.map((skill) => (
          <article className="native-card" key={skill.path}>
            <strong>{skill.name}</strong>
            <p>{skill.description}</p>
            <code>{skill.path}</code>
            <div className="native-options">
              <Button
                onClick={async () => {
                  try {
                    await store.send(`请使用 ${skill.name} 技能。`, [
                      { type: "skill", name: skill.name, path: skill.path },
                    ]);
                  } catch (e) {
                    setError(String(e));
                  }
                }}
              >
                在对话中使用
              </Button>
              <Button
                variant="outline"
                onClick={() =>
                  void act("skills/config/write", {
                    path: skill.path,
                    enabled: skill.enabled === false,
                  })
                }
              >
                {skill.enabled === false ? "启用" : "停用"}
              </Button>
            </div>
          </article>
        ))}
      {kind === "mcp" && (
        <>
          <Field label="MCP 服务名称">
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="服务配置">
            <textarea
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </Field>
          <div className="native-options">
            <Button
              onClick={() => {
                try {
                  if (!name || !/^[\w-]+$/.test(name))
                    throw new Error("使用字母、数字、下划线或连字符");
                  void act("config/value/write", {
                    keyPath: `mcp_servers.${name}`,
                    value: JSON.parse(value),
                    mergeStrategy: "replace",
                  });
                } catch (e) {
                  setError(String(e));
                }
              }}
            >
              保存服务
            </Button>
            <Button
              variant="outline"
              onClick={() => void act("config/mcpServer/reload", {})}
            >
              重新加载
            </Button>
          </div>
        </>
      )}
      {kind === "plugins" && (
        <>
          <Field label="插件名称">
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Marketplace 绝对路径（可选）">
            <input
              value={value.startsWith("{") ? "" : value}
              onChange={(e) => setValue(e.target.value)}
            />
          </Field>
          <Button
            disabled={!name || busy}
            onClick={() =>
              void act("plugin/install", {
                pluginName: name,
                ...(!value.startsWith("{") && value
                  ? { marketplacePath: value }
                  : {}),
              })
            }
          >
            安装插件
          </Button>
          <Field label="卸载插件 ID">
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Button
            variant="outline"
            disabled={!name || busy}
            onClick={() => void act("plugin/uninstall", { pluginId: name })}
          >
            卸载插件
          </Button>
        </>
      )}
      {safeUrl(data.authorizationUrl || data.authUrl || data.url) && (
        <a
          href={safeUrl(data.authorizationUrl || data.authUrl || data.url)}
          target="_blank"
          rel="noreferrer"
        >
          打开授权页面
        </a>
      )}
      <Json value={data} />
      <ErrorText error={error} />
    </div>
  );
}

function History({ state }: { state: ConsoleState }) {
  const [data, setData] = useState<Obj>({});
  const [term, setTerm] = useState("");
  const [archived, setArchived] = useState(false);
  const [agents, setAgents] = useState(false);
  const [error, setError] = useState("");
  const load = async (cursor?: string) => {
    try {
      setData(
        await native("thread/list", {
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
        }),
      );
      setError("");
    } catch (e) {
      setError(String(e));
    }
  };
  useEffect(() => {
    void load();
  }, [archived, agents]);
  return (
    <div className="native-form">
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
      <label className="native-checkbox">
        <input
          type="checkbox"
          checked={archived}
          onChange={(e) => setArchived(e.target.checked)}
        />
        已归档
      </label>
      <label className="native-checkbox">
        <input
          type="checkbox"
          checked={agents}
          onChange={(e) => setAgents(e.target.checked)}
        />
        当前会话的子 Agent
      </label>
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
                } catch (e) {
                  setError(String(e));
                }
              }}
            >
              打开会话
            </Button>
            <Button
              variant="outline"
              onClick={async () => {
                try {
                  await native(
                    archived ? "thread/unarchive" : "thread/archive",
                    { threadId: thread.id },
                  );
                  await load();
                  await store.refreshSessions();
                } catch (e) {
                  setError(String(e));
                }
              }}
            >
              {archived ? "取消归档" : "归档"}
            </Button>
          </div>
        </article>
      ))}
      {data.nextCursor && (
        <Button variant="outline" onClick={() => void load(data.nextCursor)}>
          下一页
        </Button>
      )}
      <ErrorText error={error} />
    </div>
  );
}

function TerminalFiles({
  state,
  files,
}: {
  state: ConsoleState;
  files: boolean;
}) {
  const [path, setPath] = useState(
    state.sessions.find((s) => s.id === state.active)?.cwd ||
      state.nativeProtocol?.cwd ||
      "",
  );
  const [command, setCommand] = useState("");
  const [stdin, setStdin] = useState("");
  const [content, setContent] = useState("");
  const [data, setData] = useState<Obj>({});
  const [processId, setProcessId] = useState("");
  const [error, setError] = useState("");
  const [output, setOutput] = useState("");
  const cursor = useRef("");
  useEffect(() => {
    if (!processId) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const page = await api<Obj>(
          "/api/events/history?limit=200&method=command%2Fexec%2FoutputDelta" +
            (cursor.current
              ? "&after=" + encodeURIComponent(cursor.current)
              : ""),
        );
        if (stopped) return;
        cursor.current = page.next_cursor;
        for (const event of page.events)
          if (event.payload?.params?.processId === processId)
            setOutput(
              (old) => old + decode64(event.payload.params.deltaBase64),
            );
      } catch (e) {
        if (!stopped) setError(String(e));
      }
      if (!stopped) timer = setTimeout(poll, 250);
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [processId]);
  const invoke = async (method: string, params: Obj) => {
    setError("");
    try {
      const r = await native(method, params);
      setData(r);
      if (r.dataBase64 !== undefined) setContent(decode64(r.dataBase64));
    } catch (e) {
      setError(String(e));
    }
  };
  return (
    <div className="native-form">
      <Field label={files ? "文件或目录的绝对路径" : "命令工作目录"}>
        <input value={path} onChange={(e) => setPath(e.target.value)} />
      </Field>
      {files ? (
        <>
          <div className="native-options">
            <Button onClick={() => void invoke("fs/readDirectory", { path })}>
              列出目录
            </Button>
            <Button
              variant="outline"
              onClick={() => void invoke("fs/readFile", { path })}
            >
              读取文件
            </Button>
          </div>
          <Field label="文件内容">
            <textarea
              className="native-editor"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              spellCheck={false}
            />
          </Field>
          <Button
            onClick={() =>
              void invoke("fs/writeFile", {
                path,
                dataBase64: encode64(content),
              })
            }
          >
            保存文件
          </Button>
        </>
      ) : (
        <>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const id = requestKey();
              const baseline = await api<Obj>("/api/events/history?limit=1");
              cursor.current = baseline.watermark;
              setOutput("");
              setProcessId(id);
              await invoke("command/exec", {
                command: ["/bin/sh", "-lc", command],
                cwd: path,
                processId: id,
                streamStdin: true,
                streamStdoutStderr: true,
                timeoutMs: 300000,
              });
              await new Promise((resolve) => setTimeout(resolve, 400));
              setProcessId("");
            }}
          >
            <Field label="Shell 命令">
              <textarea
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                spellCheck={false}
              />
            </Field>
            <Button disabled={!!processId || !command.trim()}>执行命令</Button>
          </form>
          {processId && (
            <>
              <p role="status">命令执行中…</p>
              <Field label="标准输入">
                <input
                  value={stdin}
                  onChange={(e) => setStdin(e.target.value)}
                />
              </Field>
              <div className="native-options">
                <Button
                  onClick={() =>
                    void invoke("command/exec/write", {
                      processId,
                      deltaBase64: encode64(stdin + "\n"),
                    })
                  }
                >
                  发送输入
                </Button>
                <Button
                  variant="outline"
                  onClick={() =>
                    void invoke("command/exec/terminate", { processId })
                  }
                >
                  停止命令
                </Button>
              </div>
            </>
          )}
          {(output || data.stdout !== undefined) && (
            <pre className="native-terminal">
              {output || data.stdout}
              {data.stderr}
            </pre>
          )}
        </>
      )}
      <Json value={data} />
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
        {protocol?.version} · {protocol?.methods.length}{" "}
        个运行时方法。此工具供高级调试使用，账号认证接口不在网页中提供。
      </p>
      <Field label="筛选方法">
        <input value={filter} onChange={(e) => setFilter(e.target.value)} />
      </Field>
      <Field label="原生方法">
        <select value={method} onChange={(e) => select(e.target.value)}>
          {(protocol?.methods || [])
            .filter(
              (m: string) => !/^account\//.test(m) && !/oauth\/login/.test(m),
            )
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
}: {
  state: ConsoleState;
  initialTab?: string;
}) {
  const tab = initialTab;
  return (
    <div id="native-control">
      {tab === "settings" ? (
        <Settings state={state} />
      ) : tab === "history" ? (
        <History state={state} />
      ) : tab === "terminal" || tab === "files" ? (
        <TerminalFiles key={tab} state={state} files={tab === "files"} />
      ) : tab === "protocol" ? (
        <ProtocolExplorer state={state} />
      ) : (
        <Library key={tab} state={state} kind={tab} />
      )}
    </div>
  );
}
