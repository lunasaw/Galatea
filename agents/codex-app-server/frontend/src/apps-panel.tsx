import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "./components/ui/button";
import { ArrowLeft, ChevronDown, ExternalLink, RefreshCw, Search } from "lucide-react";
import { allPages, Facts, Feedback, useAction, useResource } from "./capability-controls";
import { deferred } from "./deferred";
import { native, type Obj } from "./native";
import { observeNative } from "./native-events";
import { consoleStore as store, requestKey, type ConsoleState } from "./store";
import { SchemaInput, schemaDefault } from "./schema-form";
import { httpUrl, IntegrationsClient } from "./integrations-data";
import { mcpApps, type McpApp } from "./mcp-app-catalog";
const McpAppView = deferred(() => import("./mcp-app-view").then(m => ({ default: m.McpAppView })));

type NativeApp = Obj;

let sessionRefresh: Promise<void> | undefined;

function isMissingThread(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  return /thread\s+not\s+found|thread.*不存在|找不到.*thread/i.test(message);
}

function refreshSessionsOnce() {
  if (!sessionRefresh) {
    sessionRefresh = store.refreshSessions().finally(() => { sessionRefresh = undefined; });
  }
  return sessionRefresh;
}

async function withThreadFallback<T>(threadId: string | undefined, load: (id?: string) => Promise<T>) {
  try {
    return await load(threadId);
  } catch (error) {
    if (!threadId || !isMissingThread(error)) throw error;
    // A service restart can leave the browser's active session pointing at a
    // thread the runtime no longer knows. Refresh the session index, then load
    // catalog data without a thread because these methods are global.
    try { await refreshSessionsOnce(); } catch { /* the global retry is still useful */ }
    return load();
  }
}

function threadParams(threadId: string | undefined, extra: Obj = {}) {
  return threadId ? { ...extra, threadId } : extra;
}

function loadAppCatalog(threadId?: string) {
  return withThreadFallback(threadId, (id) => allPages<NativeApp>("app/list", threadParams(id)));
}

function loadInstalledApps(threadId?: string, forceRefresh = false) {
  return withThreadFallback(threadId, (id) => native<{ apps?: NativeApp[] }>("app/installed", threadParams(id, forceRefresh ? { forceRefresh: true } : {})));
}

function loadMcpServers(threadId?: string) {
  return withThreadFallback(threadId, (id) => new IntegrationsClient(native).mcps(id));
}

function loadAppDetail(threadId: string | undefined, appId: string) {
  return withThreadFallback(threadId, (id) => native<Obj>("app/read", {
    ...threadParams(id), appIds: [appId], includeTools: true,
  }));
}

function McpAppLauncher({ app, threadId, ensureThread, placement, focused, onOpen }: { app: McpApp; threadId?: string; ensureThread: (forceNew?: boolean) => Promise<string>; placement?: "panel"; focused?: boolean; onOpen?: () => void }) {
  const schema = (app.tool.inputSchema as Obj) || { type: "object" };
  const [args, setArgs] = useState(() => schemaDefault(schema));
  const [result, setResult] = useState<Obj>();
  const [invocation, setInvocation] = useState<{ id: string; args: Obj }>();
  const [boundThreadId, setBoundThreadId] = useState<string>();
  const action = useAction();
  const title = app.tool.title || app.tool.name || app.toolName;
  const activeThreadId = boundThreadId || threadId;
  return <article className="native-card apps-card" data-mcp-app={app.id}>
    <div hidden={focused}>
    <div className="integration-heading"><strong>{title}</strong><span className="integration-badge">MCP App</span></div>
    <p className="apps-card-description">{app.tool.description || "可交互的 MCP 应用"}</p>
    <div className="native-options apps-card-actions"><Button variant="secondary" size="sm" disabled={action.busy} onClick={() => void action.run(async () => {
      let launchThreadId = activeThreadId || await ensureThread();
      const id = requestKey();
      let response: Obj;
      try {
        response = await native<Obj>("mcpServer/tool/call", { threadId: launchThreadId, server: app.server, tool: app.toolName, arguments: args }, id);
      } catch (error) {
        if (isMissingThread(error)) {
          try { await refreshSessionsOnce(); } catch { /* retry with an Apps-owned session */ }
          launchThreadId = await ensureThread(true);
          response = await native<Obj>("mcpServer/tool/call", { threadId: launchThreadId, server: app.server, tool: app.toolName, arguments: args }, requestKey());
        } else {
          throw error;
        }
      }
      setBoundThreadId(launchThreadId);
      setResult(response); setInvocation({ id, args });
      if (response.isError) throw new Error("工具返回执行错误，请查看应用结果");
      onOpen?.();
    }, "应用已打开")}>打开应用</Button><span className="integration-muted">{boundThreadId ? "应用已就绪" : threadId ? "在当前聊天中打开" : "无需选择聊天"}</span></div>
    <details className="app-launch-options"><summary>应用详情<ChevronDown size={14} /></summary>
      <p>{app.tool.description || "可交互的 MCP 应用"}</p><small>{app.server} · {app.toolName} · {app.uri}</small>
      <div className="apps-launch-fields"><h4>调用参数</h4><SchemaInput schema={schema} value={args} change={setArgs} /></div>
    </details>
    </div>
    {result && invocation && activeThreadId && <div hidden={placement === "panel" && !focused}><McpAppView key={invocation.id} server={app.server} threadId={activeThreadId} uri={app.uri} instanceId={invocation.id} toolName={app.toolName} args={invocation.args} result={result} status="completed" placement={placement} /></div>}
    <Feedback error={action.error} status={focused ? undefined : action.status} />
  </article>;
}

function NativeAppCard({ app, installed, threadId, action, onDetail }: { app: NativeApp; installed?: NativeApp; threadId?: string; action: ReturnType<typeof useAction>; onDetail: (detail: Obj) => void }) {
  return <article className="native-card apps-card" data-native-app={app.id}>
    <div className="integration-heading"><strong>{app.name || app.runtimeName || app.id}</strong><span className="integration-badge">Codex App</span></div>
    <p>{app.description || "Codex CLI 原生连接器"}</p><small>{installed ? `已安装 · ${installed.enabled ? "已启用" : "已停用"} · ${installed.callable ? "可调用" : "当前不可调用"}` : "未安装"}</small>
    <div className="native-options apps-card-actions"><Button variant="secondary" size="sm" disabled={action.busy} onClick={() => void action.run(async () => {
      const result = await loadAppDetail(threadId, app.id);
      if (result.missingAppIds?.includes(app.id)) throw new Error("该应用的详情当前不可用");
      onDetail(result.apps?.[0]);
    }, "")}>查看详情</Button>{httpUrl(app.installUrl) && <Button asChild variant="secondary" size="sm"><a href={httpUrl(app.installUrl)} target="_blank" rel="noopener noreferrer">连接账户<ExternalLink /></a></Button>}</div>
  </article>;
}

export function AppsPanel({ state, scope = "global", placement }: { state: ConsoleState; scope?: "global" | "thread"; placement?: "panel" }) {
  const threadId = scope === "thread" ? state.sessions.find((s) => s.id === state.active)?.thread_id : undefined;
  const [selectedApp, setSelectedApp] = useState("");
  const [appsThreadId, setAppsThreadId] = useState<string>();
  const creatingAppsThread = useRef<Promise<string> | undefined>(undefined);
  const ensureThread = useCallback(async (forceNew = false) => {
    if (!forceNew && threadId) return threadId;
    if (!forceNew && appsThreadId) return appsThreadId;
    if (!creatingAppsThread.current) {
      creatingAppsThread.current = store.create(false).then((sessionId) => {
        if (!sessionId) throw new Error("无法创建 Apps 会话");
        const session = store.state.sessions.find((item) => item.id === sessionId);
        if (!session?.thread_id) throw new Error("Apps 会话没有有效线程");
        setAppsThreadId(session.thread_id);
        return session.thread_id;
      }).finally(() => { creatingAppsThread.current = undefined; });
    }
    return creatingAppsThread.current;
  }, [appsThreadId, threadId]);
  const catalog = useResource(useCallback(() => loadAppCatalog(threadId), [threadId]));
  const installed = useResource(useCallback(() => loadInstalledApps(threadId), [threadId]));
  const mcp = useResource(useCallback(() => scope === "thread" ? loadMcpServers(threadId) : Promise.resolve([]), [threadId, scope]));
  const [search, setSearch] = useState(""); const [detail, setDetail] = useState<Obj>(); const action = useAction(); const [eventError, setEventError] = useState("");
  const byId = new Map<string, NativeApp>((installed.data?.apps || []).map((a) => [a.id, a]));
  const apps = new Map<string, NativeApp>((catalog.data || []).map((a) => [a.id, a]));
  for (const a of byId.values()) if (!apps.has(a.id)) apps.set(a.id, { id: a.id, name: a.runtimeName || a.id });
  const nativeApps = [...apps.values()].filter((app) => `${app.name || ""} ${app.runtimeName || ""} ${app.description || ""}`.toLowerCase().includes(search.toLowerCase()));
  const allMcpApps = useMemo(() => mcpApps(mcp.data || []).filter((app) => `${app.server} ${app.toolName} ${app.tool.title || ""} ${app.tool.description || ""}`.toLowerCase().includes(search.toLowerCase())), [mcp.data, search]);
  useEffect(() => {
    let disposed = false; let stop: (() => void) | undefined;
    void observeNative("app/list/updated", () => { if (!disposed) { void catalog.refresh(); void installed.refresh(); } }, (error) => setEventError(String(error))).then((cleanup) => { if (disposed) cleanup(); else stop = cleanup; });
    return () => { disposed = true; stop?.(); };
  }, [catalog.refresh, installed.refresh]);
  return <section className="capability-page apps-catalog" data-app-open={!!selectedApp}>
    {!!selectedApp && <Button variant="secondary" size="sm" onClick={() => setSelectedApp("")}><ArrowLeft />返回应用列表</Button>}
    <div hidden={!!selectedApp} className="app-catalog-controls">
    <div className="apps-catalog-header"><h3>Apps</h3><Button variant="secondary" size="sm" aria-label="刷新应用" disabled={action.busy || catalog.loading || installed.loading || mcp.loading} onClick={() => void action.run(async () => {
      await loadInstalledApps(threadId, true); await Promise.all([catalog.refresh(), installed.refresh(), mcp.refresh()]);
    }, "Apps 已刷新")}><RefreshCw className={action.busy ? "animate-spin" : undefined} />刷新</Button></div>
    <p className="integration-muted">{scope === "thread" ? "按需打开，配合当前聊天使用。" : "连接账户，扩展 Codex 的能力。"}</p>
    <label className="apps-search"><Search size={16} /><input aria-label="搜索 Apps" placeholder="搜索应用" type="search" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
    </div>
    <Feedback error={catalog.error || installed.error || mcp.error || eventError || action.error} status={action.status} />
    {scope === "thread" && <section className="apps-catalog-group"><h4 hidden={!!selectedApp}>交互应用</h4>{allMcpApps.map((app) => <div key={app.id} hidden={!!selectedApp && selectedApp !== app.id}><McpAppLauncher app={app} threadId={threadId} ensureThread={ensureThread} placement={placement} focused={selectedApp === app.id} onOpen={placement === "panel" ? () => setSelectedApp(app.id) : undefined} /></div>)}{mcp.data && !allMcpApps.length && <p className="integration-empty">{search ? "没有匹配的应用" : "暂无可用的交互应用"}</p>}</section>}
    {(apps.size > 0 || scope === "global") && <section className="apps-catalog-group" hidden={!!selectedApp}><h4>账户连接</h4>{nativeApps.map((app) => <NativeAppCard key={app.id} app={app} installed={byId.get(app.id)} threadId={threadId} action={action} onDetail={setDetail} />)}{catalog.data && !nativeApps.length && <p className="integration-empty">没有匹配的账户连接</p>}</section>}
    {detail && <section className="native-card"><h4>{detail.name}</h4><p>{detail.description}</p><small>{detail.pluginDisplayNames?.join(" · ")}</small><ul className="capability-list">{detail.toolSummaries?.map((tool: Obj) => <li key={tool.name}><strong>{tool.title || tool.name}</strong><p>{tool.description}</p><small>{tool.isReadOnly ? "只读" : "可修改数据"} · {tool.isEnabled ? "已启用" : tool.disabledReason || "已停用"}</small></li>)}</ul>{!detail.toolSummaries?.length && <Facts value={detail} />}</section>}
  </section>;
}
