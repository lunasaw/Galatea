import { allPages } from "./capability-controls";
import { MarketplaceSettings, PluginShares, PluginSkill } from "./plugin-management";
import { SkillRoots } from "./skill-roots";
import { McpCapabilities } from "./mcp-capabilities";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "./components/ui/button";
import { native } from "./native";
import { consoleStore as store, type ConsoleState } from "./store";
import {
  IntegrationsClient, canInstall, httpUrl,
  type PluginsResponse, type Marketplace, type McpServer, type Plugin, type PluginApp, type PluginDetail,
} from "./integrations-data";

const client = new IntegrationsClient(native);
const changedEvent = "codex-integrations-changed";
export function integrationsChanged() {
  window.dispatchEvent(new Event(changedEvent));
}
const errorMessage = (error: unknown) => error instanceof Error ? error.message : String(error);
const Alert = ({ message }: { message: string }) => message
  ? <p role="alert" className="integration-error">{message}</p> : null;

// Scope by workspace/thread, discard late responses, and refresh all dependent
// inventories after plugin/skill mutations. No inventory is persisted in a browser.
function useInventory<T>(load: (fresh: boolean) => Promise<T>, poll = false) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const sequence = useRef(0);
  const inFlight = useRef(false);
  const refresh = useCallback(async (fresh = false) => {
    const version = ++sequence.current;
    inFlight.current = true;
    setLoading(true);
    try {
      const value = await load(fresh);
      if (version === sequence.current) { setData(value); setError(""); }
    } catch (error) {
      if (version === sequence.current) setError(errorMessage(error));
    } finally {
      if (version === sequence.current) { inFlight.current = false; setLoading(false); }
    }
  }, [load]);
  useEffect(() => {
    setData(undefined);
    void refresh();
    const changed = () => void refresh(true);
    window.addEventListener(changedEvent, changed);
    window.addEventListener("codex-native-resources-changed", changed);
    const timer = poll ? window.setInterval(() => {
      if (!document.hidden && !inFlight.current) void refresh();
    }, 5000) : undefined;
    return () => {
      sequence.current++;
      window.removeEventListener(changedEvent, changed);
      window.removeEventListener("codex-native-resources-changed", changed);
      window.clearInterval(timer);
    };
  }, [refresh, poll]);
  return { data, error, loading, refresh };
}

function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const lock = useRef(false);
  const run = async (action: () => Promise<void>) => {
    if (lock.current) return;
    lock.current = true;
    setBusy(true); setError(""); setStatus("");
    try { await action(); }
    catch (error) { setError(errorMessage(error)); }
    finally { lock.current = false; setBusy(false); }
  };
  return { busy, error, status, setStatus, run };
}

function Search({ value, onChange, label }: { value: string; onChange: (value: string) => void; label: string }) {
  return <input className="integration-search" type="search" aria-label={label}
    placeholder={label} value={value} onChange={(event) => onChange(event.target.value)} />;
}
function Feedback({ loading, error, status }: { loading?: boolean; error: string; status?: string }) {
  return <><Alert message={error} /><p className="integration-status" role="status">{status || (loading ? "正在加载…" : "")}</p></>;
}
function ReadonlyNotice({ editable }: { editable: boolean }) {
  return editable ? null : <p className="integration-muted">当前部署的配置只读；修改扩展需要使用开发 Host。</p>;
}

export function SkillsPanel({ state, scope = "global" }: { state: ConsoleState; scope?: "global" | "thread" }) {
  const cwd = store.cwd;
  const load = useCallback((fresh: boolean) => client.skills(cwd, fresh), [cwd]);
  const inventory = useInventory(load);
  const action = useAction();
  const [search, setSearch] = useState("");
  const [enabledOnly, setEnabledOnly] = useState(false);
  const editable = !!state.nativeProtocol?.configuration_editable;
  const skills = [...new Map((inventory.data?.data || []).flatMap((entry) => entry.skills).map((skill) => [skill.path, skill])).values()];
  const filtered = skills.filter((skill) => (!enabledOnly || skill.enabled) &&
    `${skill.name} ${skill.description} ${skill.interface?.displayName || ""}`.toLowerCase().includes(search.toLowerCase()));
  const session = state.sessions.find((session) => session.id === state.active);
  const canUse = !state.global && !state.loading && !state.creating && !state.submitting && !state.settingsBusy && (!session || session.status === "ready");
  return <div className="native-form integration-panel"><h3>Skills</h3>
    <p className="integration-muted">{scope === "thread" ? "选择 Skill，在当前聊天中调用。" : "管理 Skills 的可用范围、启用状态和配置来源。"}</p>
    <code className="integration-path">{cwd}</code>
    <div className="integration-toolbar">
      <Search label="搜索 Skills" value={search} onChange={setSearch} />
      <Button variant="outline" disabled={inventory.loading || action.busy} onClick={() => void inventory.refresh(true)}>刷新列表</Button>
    </div>
    <label className="native-checkbox"><input type="checkbox" checked={enabledOnly} onChange={(e) => setEnabledOnly(e.target.checked)} />仅显示已启用</label>
    <ReadonlyNotice editable={editable} />
    <Feedback loading={inventory.loading} error={action.error || inventory.error} status={action.status} />
    {(inventory.data?.data || []).flatMap((entry) => entry.errors || []).map((error, index) =>
      <Alert key={`${error.path}:${index}`} message={`${error.path}: ${error.message}`} />)}
    {filtered.map((skill) => <article className="native-card" key={skill.path}>
      <div className="integration-heading"><strong>{skill.interface?.displayName || skill.name}</strong>
        <span className="integration-badge">{skill.enabled ? "已启用" : "已停用"}</span>
        {skill.scope && <span className="integration-muted">{skill.scope}</span>}</div>
      <p>{skill.description}</p><code>{skill.path}</code>
      {skill.pluginId && <small>来自插件 {skill.pluginId}</small>}
      <div className="native-options">
        {scope === "thread" && <Button disabled={!skill.enabled || !canUse || action.busy} onClick={() => void action.run(async () => {
          await store.send(`请使用 ${skill.name} 技能。`, [{ type: "skill", name: skill.name, path: skill.path }]);
          if (store.state.error) throw new Error(store.state.error);
          window.dispatchEvent(new CustomEvent("codex-panel", { detail: "" }));
        })}>在对话中使用</Button>}
        {scope === "global" && <Button variant="outline" disabled={!editable || action.busy} onClick={() => void action.run(async () => {
          const result = await client.setSkill(skill, !skill.enabled);
          action.setStatus(result.effectiveEnabled ? "Skill 已启用" : "Skill 已停用");
          integrationsChanged();
        })}>{skill.enabled ? "停用" : "启用"}</Button>}
      </div>
    </article>)}
    {scope === "global" && <SkillRoots state={state} />}
    {inventory.data && !filtered.length && <p className="integration-empty">{skills.length ? "没有匹配的 Skills" : "当前目录没有可用 Skills"}</p>}
  </div>;
}

const runtimeNames: Record<string, string> = {
  notStarted: "未启动", starting: "连接中", connected: "已连接", authenticationRequired: "需要授权",
  failed: "连接失败", cancelled: "已取消", disabled: "已禁用",
};
const authNames: Record<string, string> = { unknown: "认证状态未知", unsupported: "无需 OAuth", notLoggedIn: "未登录", oAuth: "OAuth 已登录", bearerToken: "Token 认证" };
function McpRow({ server, threadId, run, busy, refresh }: {
  server: McpServer; threadId?: string; busy: boolean;
  run: (fn: () => Promise<void>) => Promise<void>; refresh: () => Promise<void>;
}) {
  const [authUrl, setAuthUrl] = useState<string>();
  const [copyStatus, setCopyStatus] = useState("");
  useEffect(() => {
    if (server.authStatus !== "notLoggedIn") { setAuthUrl(undefined); setCopyStatus(""); }
  }, [server.authStatus]);
  const login = () => {
    // Reserve the tab during the user's click, before awaiting the RPC.
    const tab = window.open("about:blank", "_blank");
    if (tab) tab.opener = null;
    setAuthUrl(undefined); setCopyStatus("");
    void run(async () => {
      try {
        const result = await client.loginMcp(server.name, threadId);
        const url = httpUrl(result.authorizationUrl);
        if (!url) throw new Error("服务未返回有效的授权地址");
        setAuthUrl(url);
        if (tab && !tab.closed) tab.location.href = url;
        await refresh();
      } catch (error) { tab?.close(); throw error; }
    });
  };
  const state = server.runtimeStatus;
  return <article className="native-card" data-mcp={server.name}>
    <div className="integration-heading"><strong>{server.name}</strong>
      <span className="integration-badge" data-state={state || "unknown"}>{runtimeNames[state || ""] || "状态未知"}</span></div>
    <p>{authNames[server.authStatus] || server.authStatus} · {Object.keys(server.tools || {}).length} 个工具
      {server.pluginId && ` · 来自插件 ${server.pluginId}`}</p>
    <Alert message={server.toolsError || ""} />
    {server.authStatus === "notLoggedIn" && <div className="native-options">
      <Button variant="outline" disabled={busy} onClick={login}>登录 {server.name}</Button>
    </div>}
    {authUrl && <div className="integration-auth">
      <p>在浏览器中完成授权；若新窗口未打开，可使用以下链接。</p>
      <a href={authUrl} target="_blank" rel="noopener noreferrer">打开授权页面</a>
      <Button size="sm" variant="ghost" onClick={() => {
        void navigator.clipboard?.writeText(authUrl).then(() => setCopyStatus("已复制授权链接"), () => setCopyStatus("复制失败，请使用授权链接"));
        if (!navigator.clipboard) setCopyStatus("请使用授权链接打开页面");
      }}>复制授权链接</Button><span role="status">{copyStatus}</span>
    </div>}
    {threadId && <details><summary>工具与资源</summary>
      <McpCapabilities key={`${server.name}:${threadId || "global"}`} server={server} threadId={threadId} />
    </details>}
  </article>;
}

export function McpPanel({ state, scope = "global" }: { state: ConsoleState; scope?: "global" | "thread" }) {
  const threadId = scope === "thread" ? state.sessions.find((session) => session.id === state.active)?.thread_id : undefined;
  const load = useCallback(() => client.mcps(threadId), [threadId, state.active]);
  const inventory = useInventory(load, true);
  const action = useAction();
  const [search, setSearch] = useState("");
  const [name, setName] = useState("");
  const [config, setConfig] = useState('{"command":"","args":[]}');
  const editable = !!state.nativeProtocol?.configuration_editable;
  const rows = inventory.data || [];
  return <div className="native-form integration-panel"><h3>MCP</h3>
    <p className="integration-muted">MCP 连接外部工具与资源。连接状态每 5 秒更新。</p>
    <div className="integration-toolbar">
      <Search label="搜索 MCP 服务" value={search} onChange={setSearch} />
      <Button variant="outline" disabled={inventory.loading || action.busy} onClick={() => void inventory.refresh()}>刷新列表</Button>
      {scope === "global" && <Button variant="outline" disabled={action.busy} onClick={() => void action.run(async () => {
        await client.reloadMcps(); action.setStatus("已请求重新加载 MCP 服务"); integrationsChanged();
      })}>重新加载全部</Button>}
    </div>
    <p className="integration-muted">{rows.filter((row) => row.runtimeStatus === "connected").length}/{rows.length} 个服务已连接</p>
    <Feedback loading={inventory.loading} error={action.error || inventory.error} status={action.status} />
    {rows.filter((row) => row.name.toLowerCase().includes(search.toLowerCase())).map((server) =>
      <McpRow key={`${threadId || "global"}:${server.name}`} server={server} threadId={threadId}
        run={action.run} busy={action.busy} refresh={inventory.refresh} />)}
    {inventory.data && !rows.length && <p className="integration-empty">当前没有 MCP 服务，请添加服务配置。</p>}
    {scope === "global" && <details><summary>添加或更新 MCP 服务</summary>
      <ReadonlyNotice editable={editable} />
      <label className="native-field">MCP 服务名称<input aria-label="MCP 服务名称" value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="native-field">服务配置<textarea aria-label="服务配置" value={config} onChange={(e) => setConfig(e.target.value)} /></label>
      <p className="integration-muted">支持 stdio command/args 或远程 url；使用 env_vars 或 bearer_token_env_var 引用环境变量。</p>
      <Button disabled={!editable || action.busy} onClick={() => void action.run(async () => {
        if (!/^[\w-]+$/.test(name.trim())) throw new Error("服务名称请使用字母、数字、下划线或连字符");
        const value = JSON.parse(config);
        if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("服务配置必须为 JSON 对象");
        if (!(typeof value.command === "string" && value.command.trim()) && !(typeof value.url === "string" && httpUrl(value.url)))
          throw new Error("请填写 command 或有效的 HTTP(S) url");
        await native("config/value/write", { keyPath: `mcp_servers.${name.trim()}`, value, mergeStrategy: "replace" });
        action.setStatus("配置已保存；正在重新加载 MCP 服务");
        await client.reloadMcps(); integrationsChanged();
      })}>保存并重新加载</Button>
    </details>}
  </div>;
}

function AppLinks({ apps }: { apps: PluginApp[] }) {
  return <ul className="integration-apps">{apps.map((app) => <li key={app.id}>
    <strong>{app.name}</strong>{app.description && <p>{app.description}</p>}
    {httpUrl(app.installUrl) && <a href={httpUrl(app.installUrl)} target="_blank" rel="noopener noreferrer">打开应用授权页面</a>}
  </li>)}</ul>;
}

export function PluginsPanel({ state }: { state: ConsoleState }) {
  const [installedOnly, setInstalledOnly] = useState(false);
  const [searchRows, setSearchRows] = useState<Marketplace[] | null>(null);
  const [sharing, setSharing] = useState(false);
  const cwd = store.cwd;
  const load = useCallback((fresh: boolean) => installedOnly ? native<PluginsResponse>("plugin/installed", { cwds: cwd ? [cwd] : [] }) : client.plugins(cwd, fresh), [cwd, installedOnly]);
  const inventory = useInventory(load);
  const action = useAction();
  const editable = !!state.nativeProtocol?.configuration_editable;
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<{ marketplace: Marketplace; plugin: Plugin }>();
  const detailRef = useRef<HTMLElement>(null);
  const [authApps, setAuthApps] = useState<PluginApp[]>([]);
  const [warnings, setWarnings] = useState("");
  const selectedId = selected?.plugin.id;
  const selectedMarketplace = selected?.marketplace;
  const selectedPlugin = selected?.plugin;
  const detailLoad = useCallback(async () => selectedMarketplace && selectedPlugin
    ? (await client.detail(selectedMarketplace, selectedPlugin)).plugin : undefined,
  [selectedMarketplace, selectedPlugin]);
  const detail = useInventory<PluginDetail | undefined>(detailLoad);
  useEffect(() => { setSelected(undefined); setAuthApps([]); setWarnings(""); }, [cwd]);
  useEffect(() => { if (selected) detailRef.current?.scrollIntoView({ block: "nearest" }); }, [selected]);
  const change = (marketplace: Marketplace, plugin: Plugin) => void action.run(async () => {
    setAuthApps([]); setWarnings("");
    if (plugin.installed) {
      await client.uninstall(plugin); action.setStatus("插件已卸载");
    } else {
      const result = await client.install(marketplace, plugin);
      setAuthApps(result.appsNeedingAuth || []);
      action.setStatus(result.appsNeedingAuth?.length ? "插件已安装，以下应用需要授权" : "插件已安装");
    }
    integrationsChanged();
  });
  const renderAction = (marketplace: Marketplace, plugin: Plugin) =>
    plugin.installPolicy === "INSTALLED_BY_DEFAULT" ? <span className="integration-badge">内置插件</span>
    : plugin.installed ? <Button variant="outline" disabled={!editable || action.busy} onClick={() => change(marketplace, plugin)}>卸载</Button>
    : canInstall(plugin) ? <Button disabled={!editable || action.busy} onClick={() => change(marketplace, plugin)}>安装</Button>
    : <span className="integration-muted">{plugin.disabledReason || "当前不可安装"}</span>;
  const featured = new Set(inventory.data?.featuredPluginIds || []);
  const total = (inventory.data?.marketplaces || []).reduce((sum, marketplace) => sum + marketplace.plugins.length, 0);
  let visible = 0;
  return <div className="native-form integration-panel"><h3>插件</h3>
    <p className="integration-muted">插件可同时提供 Skills、MCP 服务和应用。安装后相关能力列表会自动刷新。</p>
    <div className="integration-toolbar">
      <Search label="搜索插件" value={search} onChange={(value) => { setSearch(value); setSearchRows(null); }} />
      <Button variant="outline" disabled={action.busy || !search.trim()} onClick={() => void action.run(async () => {
        const results = await allPages("plugin/search", { searchTerm: search, cwds: cwd ? [cwd] : [] });
        const markets = new Map<string, Marketplace>();
        for (const r of results) { const key = r.marketplacePath || r.marketplaceName; if (!markets.has(key)) markets.set(key, { name: r.marketplaceName, path: r.marketplacePath, plugins: [] }); markets.get(key)!.plugins.push(r.plugin); }
        setSearchRows([...markets.values()]); action.setStatus(`找到 ${results.length} 个插件`);
      })}>搜索市场</Button>
      <Button variant="outline" disabled={inventory.loading || action.busy} onClick={() => void inventory.refresh(true)}>刷新市场</Button>
      <Button variant="outline" disabled={!editable || action.busy} onClick={() => void action.run(async () => {
        setWarnings("");
        const result = await client.reconcile();
        const failures = [...new Set([...result.failedRemotePluginIds, ...result.failedMaterializationRemotePluginIds])];
        setWarnings(failures.length ? `以下插件同步或下载失败：${failures.join("、")}` : "");
        action.setStatus(`同步完成，${result.changedPlugins.length} 个插件发生变化${failures.length ? "，部分失败" : ""}`);
        integrationsChanged();
      })}>同步已安装</Button>
    </div>
    <label className="native-checkbox"><input type="checkbox" checked={installedOnly} onChange={(e) => setInstalledOnly(e.target.checked)} />仅显示已安装</label>
    <ReadonlyNotice editable={editable} />
    <Feedback loading={inventory.loading} error={action.error || inventory.error} status={action.status} />
    <Alert message={warnings} /><Alert message={inventory.data?.remoteSyncError || ""} />
    {(inventory.data?.marketplaceLoadErrors || []).map((error, index) => <Alert key={`${error.marketplacePath}:${index}`} message={`${error.marketplacePath}: ${error.message}`} />)}
    {authApps.length > 0 && <AppLinks apps={authApps} />}
    {(searchRows || inventory.data?.marketplaces || []).map((marketplace: Marketplace) => {
      const plugins = marketplace.plugins.filter((plugin) => (!installedOnly || plugin.installed) &&
        (searchRows !== null || `${plugin.name} ${plugin.interface?.displayName || ""} ${plugin.interface?.shortDescription || ""}`.toLowerCase().includes(search.toLowerCase())));
      visible += plugins.length;
      return plugins.length ? <section key={marketplace.path || marketplace.name} className="integration-marketplace">
        <h3>{marketplace.interface?.displayName || marketplace.name} <small>({plugins.length})</small></h3>
        {plugins.map((plugin) => <article className="native-card" key={plugin.id} data-plugin={plugin.id}>
          <div className="integration-heading"><strong>{plugin.interface?.displayName || plugin.name}</strong>
            {featured.has(plugin.id) && <span className="integration-badge">精选</span>}
            {plugin.installed && <span className="integration-badge">{plugin.enabled ? "已安装" : "已安装 · 已停用"}</span>}</div>
          <p>{plugin.interface?.shortDescription}</p>
          <small className="integration-muted">{[plugin.interface?.developerName, plugin.interface?.category].filter(Boolean).join(" · ")}</small>
          <div className="native-options">
            <Button variant="outline" onClick={() => setSelected({ marketplace, plugin })}>查看详情</Button>
            {renderAction(marketplace, plugin)}
          </div>
        </article>)}
      </section> : null;
    })}
    {inventory.data && !visible && <p className="integration-empty">{total ? "没有匹配的插件" : "当前没有可用插件市场"}</p>}
    <MarketplaceSettings markets={inventory.data?.marketplaces || []} editable={editable} />
    <Button variant="outline" onClick={() => setSharing(!sharing)}>{sharing ? "收起共享管理" : "管理共享插件"}</Button>
    {sharing && <PluginShares />}
    {selected && <section ref={detailRef} aria-label="插件详情" className="integration-detail">
      <div className="integration-heading"><h3>{detail.data?.summary.interface?.displayName || selectedPlugin?.interface?.displayName || selectedPlugin?.name}</h3>
        <Button variant="ghost" onClick={() => setSelected(undefined)}>关闭详情</Button></div>
      <Feedback loading={detail.loading} error={detail.error} />
      {detail.data && detail.data.summary.id === selectedId && <>
        <p>{detail.data.description || detail.data.summary.interface?.longDescription}</p>
        <p className="integration-muted">{detail.data.marketplaceName} · {detail.data.summary.authPolicy === "ON_INSTALL" ? "安装时授权" : "使用时授权"}</p>
        {!!detail.data.summary.interface?.capabilities?.length && <p>能力：{detail.data.summary.interface.capabilities.join("、")}</p>}
        {!!detail.data.skills.length && <><h4>Skills</h4>{detail.data.skills.map((skill) => <div className="integration-tool" key={skill.path}>
          <strong>{skill.name}</strong><span> · {skill.enabled ? "已启用" : "已停用"}</span><p>{skill.description}</p>
          {!selected.marketplace.path && <PluginSkill marketplace={selected.marketplace.name} pluginId={(detail.data!.summary as any).shareContext?.remotePluginId || selected.plugin.id} name={skill.name} />}
        </div>)}</>}
        {!!detail.data.mcpServers.length && <><h4>MCP 服务</h4><p>{detail.data.mcpServers.join("、")}</p></>}
        {!!detail.data.apps.length && <><h4>应用</h4><AppLinks apps={detail.data.apps} /></>}
        <div className="native-options">{([
          ["网站", detail.data.summary.interface?.websiteUrl], ["隐私政策", detail.data.summary.interface?.privacyPolicyUrl],
          ["服务条款", detail.data.summary.interface?.termsOfServiceUrl],
        ] as const).filter(([, url]) => httpUrl(url)).map(([label, url]) => <a key={label} href={httpUrl(url)} target="_blank" rel="noopener noreferrer">{label}</a>)}</div>
        {renderAction(selected.marketplace, detail.data.summary)}
      </>}
    </section>}
  </div>;
}
