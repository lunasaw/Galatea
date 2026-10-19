import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "./components/ui/button";
import { allPages, Facts, Feedback, Field, useAction, useResource } from "./capability-controls";
import { native, type Obj } from "./native";
import { consoleStore as store, type ConsoleState } from "./store";
import { WorkspaceLink } from "./workspace-file";
import { CompleteConfigSettings } from "./complete-config-settings";
import { observeNative } from "./native-events";
import { SettingRow } from "./model-settings";

export function MemoryPanel({ state }: { state: ConsoleState }) {
  const memoryStatus = useResource(useCallback(() => native("memory/status", {}), []), 5000);
  const [confirm, setConfirm] = useState(false); const action = useAction();
  return <section className="capability-page"><h3>Memory</h3><p className="muted">管理此实例共享的记忆数据和默认配置。</p>
    <div className="desktop-settings-group"><SettingRow label="记忆服务" description="由当前运行实例提供。"><span>{memoryStatus.loading ? "读取中…" : memoryStatus.data?.v2Ready === true ? "已就绪" : memoryStatus.data?.v2Ready === false ? "尚未就绪" : "未提供状态"}</span></SettingRow>
      {typeof memoryStatus.data?.v2ConsolidatedThreads === "number" && <SettingRow label="已整理的聊天"><span>{memoryStatus.data.v2ConsolidatedThreads}</span></SettingRow>}</div><Feedback error={memoryStatus.error} />
    <details><summary>记忆、目标与智能体配置</summary><CompleteConfigSettings state={state} only={["memories", "goals", "agents"]} /></details>
    <div><h4>重置所有记忆</h4><p className="muted">清除运行实例的记忆数据，影响此实例下的所有会话。</p></div>
    {confirm ? <div className="native-options"><Button variant="destructive" disabled={action.busy} onClick={() => void action.run(async () => {
      await native("memory/reset", null); setConfirm(false);
    }, "记忆已重置")}>确认重置所有记忆</Button><Button variant="ghost" onClick={() => setConfirm(false)}>取消</Button></div>
      : <Button variant="outline" onClick={() => setConfirm(true)}>重置记忆…</Button>}
    <Feedback error={action.error} status={action.status} />
  </section>;
}

export function HooksPanel({ state }: { state: ConsoleState }) {
  const cwd = store.cwd; const load = useCallback(() => native("hooks/list", { cwds: cwd ? [cwd] : [] }), [cwd]);
  const resource = useResource(load);
  return <section className="capability-page"><h3>Hooks</h3><p className="muted">查看事件触发的命令、信任状态和共享配置。</p><Button variant="outline" onClick={() => void resource.refresh()}>刷新配置</Button>
    {resource.data?.data?.map((entry: Obj, index: number) => <section key={index}><p className="muted">{entry.cwd}</p>
      {entry.errors?.map((error: Obj, i: number) => <p role="alert" key={i}>{error.path}: {error.message}</p>)}
      {entry.warnings?.map((warning: string, i: number) => <p key={i}>{warning}</p>)}
      <ul className="capability-list">{entry.hooks?.map((hook: Obj) => <li key={hook.key}>
        <strong>{hook.eventName}</strong><span> · {hook.enabled ? "已启用" : "已停用"} · {hook.trustStatus}</span>
        <p><code>{hook.command || [hook.server, hook.tool].filter(Boolean).join(" / ") || hook.handlerType}</code></p>
        <WorkspaceLink href={hook.sourcePath}>{hook.sourcePath}</WorkspaceLink><p className="muted">匹配：{hook.matcher || "全部"} · 超时 {hook.timeoutSec} 秒</p>
        {hook.statusMessage && <p>{hook.statusMessage}</p>}
      </li>)}</ul>{!entry.hooks?.length && <p>此目录没有 Hooks</p>}</section>)}
    <CompleteConfigSettings state={state} only={["hooks"]} />

    <Feedback error={resource.error} status={resource.loading ? "读取中…" : ""} />
  </section>;
}

export function RemotePanel() {
  const load = useCallback(() => native("remoteControl/status/read", null), []); const resource = useResource(load, 5000);
  const environmentId = resource.data?.environmentId; const action = useAction();
  const loadClients = useCallback(() => environmentId ? allPages("remoteControl/client/list", { environmentId }) : Promise.resolve([]), [environmentId]);
  const clients = useResource(loadClients, 5000); const [pairing, setPairing] = useState<Obj>(); const [claimed, setClaimed] = useState(false);
  const [expired, setExpired] = useState(false); const [pairingError, setPairingError] = useState("");
  useEffect(() => {
    if (!pairing || claimed) return;
    let alive = true; let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (Date.now() >= pairing.expiresAt * 1000) { setExpired(true); return; }
      try {
        const result = await native("remoteControl/pairing/status", { pairingCode: pairing.pairingCode });
        if (!alive) return;
        setPairingError("");
        if (result.claimed) { setClaimed(true); void clients.refresh(); return; }
      } catch (error) { if (alive) setPairingError(String(error)); }
      if (alive) timer = setTimeout(poll, 3000);
    };
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [pairing, claimed, clients.refresh]);
  const labels: Obj = { disabled: "未启用", connecting: "连接中", connected: "已连接", errored: "连接失败" };
  return <section className="capability-page"><h3>远程控制</h3><p className="muted">连接其他设备，并管理已配对客户端的访问权限。</p>
    <dl className="capability-facts"><div><dt>状态</dt><dd>{labels[resource.data?.status] || "读取中"}</dd></div><div><dt>服务器</dt><dd>{resource.data?.serverName || "—"}</dd></div><div><dt>环境</dt><dd>{environmentId || "—"}</dd></div></dl>
    <div className="native-options"><Button disabled={action.busy || !resource.data} onClick={() => void action.run(async () => {
      await native(resource.data?.status === "disabled" ? "remoteControl/enable" : "remoteControl/disable", null); await resource.refresh();
    })}>{resource.data?.status === "disabled" ? "启用远程控制" : "停用远程控制"}</Button>
      <Button variant="outline" disabled={action.busy || resource.data?.status !== "connected"} onClick={() => void action.run(async () => {
        setPairing(await native("remoteControl/pairing/start", { manualCode: true })); setClaimed(false); setExpired(false); setPairingError("");
      }, "配对码已生成")}>配对新客户端</Button></div>
    {pairing && <div><h4>配对码</h4><code className="pairing-code">{pairing.manualPairingCode || pairing.pairingCode}</code><p role="status">{claimed ? "已配对" : expired ? "配对码已过期，请重新生成" : `有效期至 ${new Date(pairing.expiresAt * 1000).toLocaleString()}`}</p>
      <Button variant="ghost" disabled={expired || action.busy} onClick={() => void action.run(async () => { setClaimed((await native("remoteControl/pairing/status", { pairingCode: pairing.pairingCode })).claimed); }, "配对状态已刷新")}>检查配对状态</Button></div>}
    <h4>已连接客户端</h4><ul className="capability-list">{clients.data?.map((client) => <li key={client.clientId}><strong>{client.displayName || client.deviceModel || client.clientId}</strong><p className="muted">{client.platform} {client.osVersion} · {client.appVersion}</p>
      <Button variant="outline" size="sm" disabled={action.busy} onClick={() => void action.run(async () => { await native("remoteControl/client/revoke", { environmentId, clientId: client.clientId }); await clients.refresh(); })}>撤销客户端访问</Button></li>)}</ul>
    <Feedback error={resource.error || clients.error || action.error || pairingError} status={action.status} />
  </section>;
}

export function EnvironmentPanel({ state }: { state: ConsoleState }) {
  const [id, setId] = useState(""); const [url, setUrl] = useState(""); const [info, setInfo] = useState<Obj>(); const [status, setStatus] = useState<Obj>(); const action = useAction();
  const [cwd, setCwd] = useState("");
  const inspect = async () => { const [a, b] = await Promise.all([native("environment/info", { environmentId: id }), native("environment/status", { environmentId: id })]); setInfo(a); setStatus(b); };
  return <section className="capability-page"><h3>执行环境</h3><p className="muted">连接 app-server 可访问的执行环境。</p>
    <Field label="环境 ID"><input value={id} onChange={(e) => { setId(e.target.value); setInfo(undefined); setStatus(undefined); }} /></Field>
    <Field label="执行服务器 WebSocket 地址"><input placeholder="wss://…" value={url} onChange={(e) => setUrl(e.target.value)} /></Field>
    <div className="native-options"><Button disabled={action.busy || !id.trim() || !/^wss?:\/\//.test(url)} onClick={() => void action.run(async () => { await native("environment/add", { environmentId: id, execServerUrl: url }); await inspect(); }, "环境已连接")}>添加环境</Button>
      <Button variant="outline" disabled={action.busy || !id.trim()} onClick={() => void action.run(inspect, "环境信息已刷新")}>查看环境</Button></div>
    {status && <p role="status">{status.status}{status.error && ` · ${status.error}`}</p>}
    {info && <><dl className="capability-facts"><div><dt>工作目录</dt><dd>{info.cwd || "未指定"}</dd></div><div><dt>Shell</dt><dd>{info.shell?.name} · {info.shell?.path}</dd></div></dl>
      <Field label="环境内工作目录"><input value={cwd} onChange={(e) => setCwd(e.target.value)} placeholder="环境中的绝对路径" /></Field>
      <Button disabled={action.busy || status?.status !== "ready" || !cwd.trim()} onClick={() => void action.run(async () => {
        const result = await native("thread/start", { environments: [{ environmentId: id, cwd }], ...(store.composerSettings.model ? { model: store.composerSettings.model } : {}) });
        await store.refreshSessions(); if (result.session) await store.select(result.session.id);
      }, "已在环境中新建会话")}>使用此环境新建会话</Button></>}
    <Feedback error={action.error} status={action.status} />
  </section>;
}

export function ImportPanel({ state }: { state: ConsoleState }) {
  const [home, setHome] = useState(true); const [source, setSource] = useState(""); const [items, setItems] = useState<Obj[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set()); const [detectedSource, setDetectedSource] = useState(""); const [importId, setImportId] = useState("");
  const action = useAction(); const load = useCallback(() => native("externalAgentConfig/import/readHistories", null), []); const history = useResource(load, importId ? 3000 : 0);
  useEffect(() => {
    if (importId && history.data?.data?.some((row: Obj) => row.importId === importId)) setImportId("");
  }, [importId, history.data]);
  return <section className="capability-page"><h3>导入外部配置</h3><p className="muted">发现其他工具的配置，选择要导入的内容并查看导入结果。</p>
    <Field label="迁移来源（留空使用运行时默认来源）"><input value={source} onChange={(e) => setSource(e.target.value)} /></Field>
    <label className="native-checkbox"><input type="checkbox" checked={home} onChange={(e) => setHome(e.target.checked)} />包含用户主目录</label>
    <Button disabled={action.busy} onClick={() => void action.run(async () => {
      const result = await native("externalAgentConfig/detect", { includeHome: home, cwds: store.cwd ? [store.cwd] : [], ...(source ? { migrationSource: source } : {}) });
      setItems(result.items); setSelected(new Set()); setDetectedSource(source);
    }, "发现完成，请选择需要导入的内容")}>发现可导入内容</Button>
    <ul className="capability-list">{items.map((item, i) => <li key={i}><label className="native-checkbox"><input type="checkbox" checked={selected.has(i)} onChange={(e) => setSelected((old) => { const next = new Set(old); if (e.target.checked) next.add(i); else next.delete(i); return next; })} /><strong>{item.itemType}</strong></label><p>{item.description}</p><small>{item.cwd || "用户主目录"}</small></li>)}</ul>
    <Button disabled={action.busy || !!importId || !selected.size || !state.nativeProtocol?.configuration_editable} onClick={() => void action.run(async () => {
      const result = await native("externalAgentConfig/import", { migrationItems: items.filter((_, i) => selected.has(i)), ...(detectedSource ? { migrationSource: detectedSource } : {}) });
      setImportId(result.importId); await history.refresh();
    }, "导入已开始")}>导入所选 {selected.size} 项</Button>
    {importId && <p role="status">导入任务：{importId}</p>}
    <h4>导入历史</h4><ul className="capability-list">{history.data?.data?.map((row: Obj) => <li key={row.importId}><strong>{row.providerId || row.importId}</strong><p>{new Date(row.completedAtMs).toLocaleString()}</p>
      <p>成功 {row.successes?.length || 0} 项 · 失败 {row.failures?.length || 0} 项</p>
      <ul>{row.successes?.map((item: Obj, i: number) => <li key={i}>{item.title || item.itemType}{item.target && <> · <WorkspaceLink href={item.target}>{item.target}</WorkspaceLink></>}</li>)}</ul>
      <ul>{row.failures?.map((item: Obj, i: number) => <li key={i} role="alert">{item.itemType} · {item.failureStage}：{item.message}</li>)}</ul>
    </li>)}</ul>
    <Feedback error={action.error || history.error} status={action.status} />
  </section>;
}

export function DiagnosticsPanel({ state, thread = false }: { state: ConsoleState; thread?: boolean }) {
  const load = useCallback(() => native("server/diagnostics"), []); const resource = useResource(load); const action = useAction();
  const [reason, setReason] = useState(""); const [logs, setLogs] = useState(false); const [classification, setClassification] = useState("bug");
  return <section className="capability-page"><h3>{thread ? "会话反馈" : "诊断与反馈"}</h3><p className="muted">{thread ? "针对当前聊天提交问题与使用反馈。" : "查看运行实例状态，或提交不关联聊天的反馈。"}</p><Button variant="outline" onClick={() => void resource.refresh()}>刷新诊断</Button>
    {resource.data && <><h4>原生服务进程</h4><Facts value={resource.data.process} /><h4>运行指标</h4><Facts value={resource.data.gauges} /></>}
    <form onSubmit={(e) => { e.preventDefault(); void action.run(async () => {
      const result = await native("feedback/upload", { classification, reason, includeLogs: logs, threadId: thread ? state.sessions.find((s) => s.id === state.active)?.thread_id || null : null });
      if (result.threadId) setReason("");
    }, "反馈已上传"); }}><h4>发送反馈</h4><Field label="反馈类型"><select value={classification} onChange={(e) => setClassification(e.target.value)}><option value="bug">问题报告</option><option value="feedback">使用反馈</option></select></Field>
      <Field label="问题描述"><textarea required value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      <label className="native-checkbox"><input type="checkbox" checked={logs} onChange={(e) => setLogs(e.target.checked)} />随反馈上传日志</label><Button disabled={action.busy || !reason.trim()}>上传反馈</Button></form>
    <Feedback error={resource.error || action.error} status={action.status} />
  </section>;
}

export function WindowsSandboxPanel() {
  const load = useCallback(() => native("windowsSandbox/readiness", null), []); const resource = useResource(load); const action = useAction(); const [mode, setMode] = useState("unelevated");
  const [completion, setCompletion] = useState<Obj>(); const [pending, setPending] = useState(false);
  const [eventError, setEventError] = useState(""); const cleanup = useRef<(() => void) | undefined>(undefined); const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; cleanup.current?.(); }; }, []);
  return <section className="capability-page"><h3>Windows sandbox</h3><p className="muted">此功能要求 app-server 运行在 Windows 主机上。</p>
    {resource.data && <p role="status">{{ ready: "已就绪", notConfigured: "尚未配置", updateRequired: "需要更新" }[resource.data.status as string] || resource.data.status}</p>}
    <Field label="安装模式"><select value={mode} onChange={(e) => setMode(e.target.value)}><option value="unelevated">普通权限</option><option value="elevated">提升权限</option></select></Field>
    <Button disabled={action.busy || pending || !resource.data} onClick={() => void action.run(async () => {
      cleanup.current?.(); setCompletion(undefined); setEventError(""); setPending(true);
      cleanup.current = await observeNative("windowsSandbox/setupCompleted", (result) => {
        if (result.mode !== mode || !mounted.current) return;
        setCompletion(result); setPending(false); cleanup.current?.(); void resource.refresh();
      }, (error) => { if (mounted.current) { setEventError(String(error)); setPending(false); } });
      if (!mounted.current) { cleanup.current(); return; }
      try {
        const result = await native("windowsSandbox/setupStart", { mode, cwd: store.cwd || null });
        if (!result.started) throw new Error("当前主机未启动 Windows sandbox 配置");
      } catch (error) { setPending(false); cleanup.current?.(); throw error; }
    }, "")}>开始配置</Button>
    <Button variant="ghost" onClick={() => void resource.refresh()}>刷新就绪状态</Button>
    <Feedback error={resource.error || action.error || eventError || (completion && !completion.success ? completion.error || "配置失败" : "")}
      status={pending ? "正在配置，请查看主机提示" : completion?.success ? "Windows sandbox 配置完成" : ""} />
  </section>;
}

export function RuntimeSettings({ tab, state }: { tab: string; state: ConsoleState }) {
  return tab === "memory" ? <MemoryPanel key={state.active || "new"} state={state} /> : tab === "hooks" ? <HooksPanel state={state} />
    : tab === "remote" ? <RemotePanel /> : tab === "environments" ? <EnvironmentPanel state={state} /> : tab === "import" ? <ImportPanel state={state} />
    : tab === "windows" ? <WindowsSandboxPanel /> : <DiagnosticsPanel state={state} />;
}

export function ThreadMemory({ state }: { state: ConsoleState }) {
  const session = state.sessions.find((s) => s.id === state.active);
  const [mode, setMode] = useState(session?.settings?.memoryMode || "");
  const action = useAction();
  return <section className="capability-page"><h3>会话记忆</h3><p className="muted">仅修改当前聊天是否使用记忆。</p>
    <Field label="会话记忆模式"><select value={mode} disabled={!session?.thread_id || action.busy} onChange={(e) => {
      const value = e.target.value; if (!value) return;
      void action.run(async () => { await native("thread/memoryMode/set", { threadId: session!.thread_id, mode: value }); setMode(value); await store.refreshSessions(); });
    }}><option value="" disabled>选择模式</option><option value="enabled">启用记忆</option><option value="disabled">停用记忆</option></select></Field>
    <Feedback error={action.error} status={action.status} /></section>;
}
export function ThreadHooks({ state }: { state: ConsoleState }) {
  return <section className="capability-page"><h3>Hooks 执行记录</h3><p className="muted">当前聊天已加载的 Hooks 运行记录。</p>
    <h4>当前会话执行记录</h4><ul className="capability-list">{state.snapshot?.steps?.filter((s) => s.type === "hook").map((step) => <li key={step.id}><Facts value={step.detail} /></li>)}</ul>    {!state.snapshot?.steps?.some((s) => s.type === "hook") && <p className="muted">暂无执行记录。</p>}</section>;
}
