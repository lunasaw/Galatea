import { useCallback, useState, useEffect } from "react";
import { Button } from "./components/ui/button";
import { allPages, Facts, Feedback, useAction, useResource } from "./capability-controls";
import { native, sandboxPolicy, type Obj } from "./native";
import { consoleStore as store, type ConsoleState } from "./store";
import type { ReactNode } from "react";
import { supportsLegacyUntrusted } from "./configuration-data";

export function SettingRow({ label, description, children }: { label: string; description?: string; children: ReactNode }) {
  return <div className="desktop-setting-row"><div><label>{label}</label>{description && <p>{description}</p>}</div><div className="desktop-setting-control">{children}</div></div>;
}
const optionLabels: Obj = { minimal: "最低", low: "低", medium: "中", high: "高", xhigh: "极高", max: "最高", ultra: "Ultra", none: "关闭", friendly: "友好", pragmatic: "务实", auto: "自动", concise: "简洁", detailed: "详细", user: "由我审批", auto_review: "自动审批", guardian_subagent: "自动审批（兼容）" };
const sandboxName = (policy?: Obj) => ({ readOnly: "read-only", workspaceWrite: "workspace-write", dangerFullAccess: "danger-full-access" } as Obj)[policy?.type];
function Select({ label, value, set, options, disabled = false }: { label: string; value: string; set: (v: string) => void; options: string[]; disabled?: boolean }) {
  return <select aria-label={label} value={value} onChange={(e) => set(e.target.value)} disabled={disabled}>
    {[...new Set([value, ...options])].map((v) => <option key={v} value={v} disabled={!options.includes(v)}>{optionLabels[v] || v || "默认"}</option>)}
  </select>;
}
export function ModelSettings({ state }: { state: ConsoleState }) {
  const session = state.sessions.find((s) => s.id === state.active); const defaults = state.runtimeDefaults;
  const [scope, setScope] = useState(session?.active_turn_id ? "turn" : "thread");
  useEffect(() => { if (!session?.active_turn_id) setScope("thread"); }, [session?.active_turn_id]);
  const settings = { ...session?.settings, ...(scope === "turn" ? session?.turn_settings : {}) };
  const [model, setModel] = useState(settings.model || store.composerSettings.model);
  const [effort, setEffort] = useState(settings.effort || store.composerSettings.effort);
  const [tier, setTier] = useState(settings.serviceTier || ""); const [personality, setPersonality] = useState(settings.personality || defaults.personality || "none");
  const [summary, setSummary] = useState(settings.summary || defaults.model_reasoning_summary || "auto");
  const [cwd, setCwd] = useState(store.cwd); const [profile, setProfile] = useState(settings.permissions || "");
  const [sandbox, setSandbox] = useState(sandboxName(settings.sandboxPolicy) || defaults.sandbox_mode || "workspace-write");
  const [sandboxChanged, setSandboxChanged] = useState(false);
  const originalApproval = settings.approvalPolicy || defaults.approval_policy || "on-request";
  const [approval, setApproval] = useState(typeof originalApproval === "string" ? originalApproval : "granular");
  const [granular, setGranular] = useState<Obj>(typeof originalApproval === "object" ? originalApproval.granular : { sandbox_approval: true, rules: true, mcp_elicitations: true, request_permissions: false, skill_approval: false });
  const [reviewer, setReviewer] = useState(settings.approvalsReviewer || defaults.approvals_reviewer || "user"); const [preset, setPreset] = useState("");
  const action = useAction();
  const requirements = useResource(useCallback(() => native("configRequirements/read", null), []));
  const profiles = useResource(useCallback(() => allPages("permissionProfile/list"), []));
  const modes = useResource(useCallback(() => native("collaborationMode/list"), []));
  const req = requirements.data?.requirements;
  const personalityDeprecated = String((state.nativeProtocol?.definitions.Personality as Obj)?.description || "").includes("Deprecated");
  const approvalOptions = (req?.allowedApprovalPolicies || ["untrusted", "on-request", "never", "granular"])
    .map((v: unknown) => typeof v === "string" ? v : "granular")
    .filter((v: string) => v !== "untrusted" || supportsLegacyUntrusted(state.nativeProtocol?.version));
  const live = scope === "turn" && !!session?.active_turn_id;
  useEffect(() => {
    const current = { ...session?.settings, ...(scope === "turn" ? session?.turn_settings : {}) };
    setModel(current.model || store.composerSettings.model); setEffort(current.effort || store.composerSettings.effort);
    setSummary(current.summary || defaults.model_reasoning_summary || "auto"); setTier(current.serviceTier || "");
    setReviewer(current.approvalsReviewer || defaults.approvals_reviewer || "user");
    setPersonality(current.personality || defaults.personality || "none");
    setCwd(session?.cwd || store.cwd); setProfile(current.permissions || "");
    setSandbox(sandboxName(current.sandboxPolicy) || defaults.sandbox_mode || "workspace-write");
    const policy = current.approvalPolicy || defaults.approval_policy || "on-request";
    setApproval(typeof policy === "string" ? policy : "granular");
    setGranular(typeof policy === "object" ? policy.granular : { sandbox_approval: true, rules: true, mcp_elicitations: true, request_permissions: false, skill_approval: false });
    setSandboxChanged(false); setPreset("");
  }, [scope, session?.id]);
  const save = async (create = false) => {
    if (!create && scope === "turn" && !session?.active_turn_id) throw new Error("当前回复已结束，请选择后续回复设置");
    const common = { model, ...(effort ? { effort } : {}), summary, serviceTier: tier.trim() || null, approvalsReviewer: reviewer };
    if (live && !create) {
      if (!await store.updateTurnSettings(session!.id, common)) throw new Error(store.state.error || "当前回复设置未应用");
      return;
    }
    const mode = modes.data?.data?.find((m: Obj) => m.name === preset);
    const values = { ...common, cwd, ...(!personalityDeprecated ? { personality } : {}),
      ...(profile ? { permissions: profile } : { sandboxPolicy: !sandboxChanged && settings.sandboxPolicy
        ? settings.sandboxPolicy : sandboxPolicy(sandbox) }),
      approvalPolicy: approval === "granular" ? { granular } : approval,
      ...(mode?.mode ? { collaborationMode: { mode: mode.mode, settings: { model, reasoning_effort: effort, developer_instructions: null } } } : {}),
    };
    let threadId = session?.thread_id;
    if (create || !threadId) {
      const result = await native("thread/start", { cwd, model, ...(profile ? { permissions: profile } : { sandbox }), approvalPolicy: values.approvalPolicy, approvalsReviewer: reviewer });
      threadId = result.session.thread_id;
      await store.refreshSessions(); await store.select(result.session.id);
    }
    await native("thread/settings/update", { threadId, ...values }); await store.refreshSessions();
  };
  return <section className="capability-page model-settings"><h3>模型与权限</h3>
    <p className="muted">{session ? `当前会话 · ${store.title(session.id)}` : "设置新会话的运行方式"}</p>
    {session?.active_turn_id && <div className="terminal-kind-tabs" role="tablist" aria-label="设置作用范围">
      <button role="tab" aria-selected={scope === "turn"} onClick={() => setScope("turn")}>只改当前回复</button>
      <button role="tab" aria-selected={scope === "thread"} onClick={() => setScope("thread")}>用于后续回复</button></div>}
    <h4>回复</h4><div className="desktop-settings-group">
      <SettingRow label="模型"><input aria-label="模型" list="native-models" value={model} onChange={(e) => setModel(e.target.value)} /><datalist id="native-models">{state.models.map((m) => <option key={m.model} value={m.model}>{m.displayName}</option>)}</datalist></SettingRow>
      <SettingRow label="推理强度" description="ultra 使用运行时提供的主动多 Agent 行为。"><Select label="推理强度" value={effort} set={setEffort} options={store.reasoningEfforts(state.models.find((m) => m.model === model), effort)} /></SettingRow>
      <SettingRow label="服务等级" description="留空清除当前等级，由服务端决定。"><input aria-label="服务等级" value={tier} onChange={(e) => setTier(e.target.value)} placeholder="默认" /></SettingRow>
      <SettingRow label="推理摘要"><Select label="推理摘要" value={summary} set={setSummary} options={["auto", "concise", "detailed", "none"]} /></SettingRow>
      {!live && !personalityDeprecated && <SettingRow label="交流风格"><Select label="交流风格" value={personality} set={setPersonality} options={["none", "friendly", "pragmatic"]} /></SettingRow>}
      {!live && <SettingRow label="工作模式"><select aria-label="工作模式" value={preset} onChange={(e) => { setPreset(e.target.value); const mode = modes.data?.data?.find((m: Obj) => m.name === e.target.value); if (mode?.model) setModel(mode.model); if (mode?.reasoning_effort) setEffort(mode.reasoning_effort); }}><option value="">保持当前模式</option>{modes.data?.data?.map((m: Obj) => <option key={m.name} value={m.name}>{m.name}</option>)}</select></SettingRow>}
    </div>
    <h4>权限</h4><div className="desktop-settings-group">
      <SettingRow label="审批方式"><Select label="审批方式" value={reviewer} set={setReviewer} options={req?.allowedApprovalsReviewers || ["user", "auto_review"]} /></SettingRow>
      {!live && <>
        <SettingRow label="工作目录"><input aria-label="工作目录" value={cwd} onChange={(e) => setCwd(e.target.value)} /></SettingRow>
        <SettingRow label="权限配置"><select aria-label="权限配置" value={profile} onChange={(e) => setProfile(e.target.value)}><option value="">使用沙箱设置</option>{profiles.data?.map((p) => <option key={p.id} value={p.id} disabled={!p.allowed}>{p.description || p.id}{!p.allowed ? "（策略禁止）" : ""}</option>)}</select></SettingRow>
        {!profile && <SettingRow label="文件与命令权限"><select aria-label="文件与命令权限" value={sandbox} onChange={(e) => { setSandbox(e.target.value); setSandboxChanged(true); }}>{[["read-only", "只读"], ["workspace-write", "允许工作区写入"], ["danger-full-access", "完全访问"]].map(([v, label]) => <option key={v} value={v} disabled={req?.allowedSandboxModes && !req.allowedSandboxModes.includes(v)}>{label}</option>)}</select></SettingRow>}
        <SettingRow label="审批策略"><Select label="审批策略" value={approval} set={setApproval} options={approvalOptions} /></SettingRow>
        {approval === "granular" && Object.entries({ sandbox_approval: "沙箱越界审批", rules: "命令规则审批", mcp_elicitations: "MCP 交互审批", request_permissions: "额外权限申请", skill_approval: "Skill 审批" }).map(([key, label]) => <SettingRow key={key} label={label}><input type="checkbox" role="switch" aria-label={label} checked={!!granular[key]} onChange={(e) => setGranular({ ...granular, [key]: e.target.checked })} /></SettingRow>)}
      </>}
    </div>
    <div className="native-options"><Button variant="primary" disabled={action.busy || !model || !cwd} onClick={() => void action.run(() => save(), live ? "已应用到当前回复，后续回复设置保持不变" : "会话设置已应用")}>{live ? "应用到当前回复" : session ? "应用到当前会话" : "创建会话"}</Button>
      {session && <Button variant="outline" disabled={action.busy} onClick={() => void action.run(() => save(true), "已新建会话")}>以此设置新建</Button>}</div>
    <details><summary>组织与运行环境要求</summary>{req ? <Facts value={req} /> : <p>未配置额外要求</p>}</details>
    <Feedback error={action.error || requirements.error || profiles.error || modes.error} status={action.status} />
  </section>;
}

export function ModelDefaults({ state }: { state: ConsoleState }) {
  const cwd = store.cwd;
  const [featureSearch, setFeatureSearch] = useState("");
  const [webSearch, setWebSearch] = useState(state.runtimeDefaults.web_search || "live");
  const action = useAction();
  const requirements = useResource(useCallback(() => native("configRequirements/read", null), []));
  const capabilities = useResource(useCallback(() => native("modelProvider/capabilities/read"), []));
  const features = useResource(useCallback(() => allPages("experimentalFeature/list"), []));
  const req = requirements.data?.requirements;
  return <section className="capability-page"><h3>模型与实验功能</h3>
    <p className="muted">查看服务商能力，管理运行实例的联网搜索和实验功能。模型默认值在“配置”中管理。</p>
    <h4>服务商能力</h4><div className="desktop-settings-group">{capabilities.data && Object.entries({ imageGeneration: "图像生成", namespaceTools: "命名空间工具", webSearch: "联网搜索" }).map(([key, name]) => <SettingRow key={key} label={name}><span>{typeof capabilities.data![key] !== "boolean" ? "未公布" : capabilities.data![key] ? "支持" : "不支持"}</span></SettingRow>)}</div>
    <h4>联网搜索</h4><div className="desktop-settings-group"><SettingRow label="搜索方式" description="保存到运行实例配置。"><select aria-label="联网搜索" value={webSearch} onChange={(e) => setWebSearch(e.target.value)}>{["disabled", "cached", "indexed", "live"].map((v) => <option key={v} disabled={req?.allowedWebSearchModes && !req.allowedWebSearchModes.includes(v)}>{v}</option>)}</select></SettingRow></div>
    <Button variant="outline" disabled={action.busy || !state.nativeProtocol?.configuration_editable} onClick={() => void action.run(async () => {
      const snapshot = await native("config/read", { includeLayers: true, cwd });
      const layer = snapshot.layers?.find((row: Obj) => row.name.type === "user");
      if (!layer) throw new Error("未找到可写用户配置");
      await native("config/batchWrite", { edits: [{ keyPath: "web_search", value: webSearch, mergeStrategy: "replace" }],
        filePath: layer.name.file, expectedVersion: layer.version, reloadUserConfig: true });
      await store.refreshRuntimeDefaults();
    }, "联网搜索配置已保存并请求重新加载")}>保存联网搜索</Button>
    <h4>实验功能</h4><input className="settings-feature-search" aria-label="搜索实验功能" placeholder="搜索功能" value={featureSearch} onChange={(e) => setFeatureSearch(e.target.value)} />
    <div className="desktop-settings-group">{features.data?.filter((f) => `${f.name} ${f.displayName}`.toLowerCase().includes(featureSearch.toLowerCase())).map((f) => <SettingRow key={f.name} label={f.displayName || f.name} description={f.description || f.stage}><input type="checkbox" role="switch" aria-label={f.displayName || f.name} checked={f.enabled} disabled={action.busy || !state.nativeProtocol?.configuration_editable || f.stage === "removed"} onChange={(e) => void action.run(async () => { await native("experimentalFeature/enablement/set", { enablement: { [f.name]: e.target.checked } }); await features.refresh(); })} /></SettingRow>)}</div>
    <Feedback error={action.error || requirements.error || capabilities.error || features.error} status={action.status} />
  </section>;
}
