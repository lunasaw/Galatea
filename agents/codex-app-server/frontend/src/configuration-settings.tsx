import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Button } from "./components/ui/button";
import { Feedback, useAction, useResource } from "./capability-controls";
import { native, type Obj } from "./native";
import { consoleStore as store, type ConsoleState } from "./store";
import { SettingRow } from "./model-settings";
import { WorkspaceFileView } from "./workspace-file";
import { effortNames } from "./slash-commands";
import { configEdits, configPath, configValue, configWriteParams, isRedacted, layerLabel, positiveInteger, supportsLegacyUntrusted, type ConfigSnapshot } from "./configuration-data";
import "./configuration-settings.css";
import { CompleteConfigSettings } from "./complete-config-settings";

const labels: Record<string, string> = { "on-request": "按请求", untrusted: "不受信任时", never: "从不", granular: "自定义审批", "read-only": "只读", "workspace-write": "允许工作区写入", "danger-full-access": "完全访问", disabled: "关闭", cached: "缓存", indexed: "索引", live: "实时", auto: "自动", concise: "简洁", detailed: "详细", none: "关闭", low: "低", medium: "中", high: "高", user: "由我审批", auto_review: "自动审批", friendly: "友好", pragmatic: "务实" };
const granularLabels = { sandbox_approval: "沙箱越界审批", rules: "命令规则审批", mcp_elicitations: "MCP 交互审批", request_permissions: "额外权限申请", skill_approval: "Skill 审批" };
const defaultGranular = { sandbox_approval: true, rules: true, mcp_elicitations: true, request_permissions: false, skill_approval: false };
const numbers = ["model_context_window", "model_auto_compact_token_limit"];

function JsonSetting({ label, value, change }: { label: string; value: unknown; change: (value: unknown) => void }) {
  const [text, setText] = useState(value == null ? "" : JSON.stringify(value, null, 2));
  return <textarea aria-label={label} className="configuration-json" rows={5} value={text} spellCheck={false} onChange={(event) => {
    setText(event.target.value);
    try {
      const parsed = event.target.value.trim() ? JSON.parse(event.target.value) : null;
      if (parsed !== null && (typeof parsed !== "object" || Array.isArray(parsed))) throw new Error("请输入 JSON 对象，或留空恢复默认");
      if (isRedacted(parsed)) throw new Error("请重新输入隐藏字段的实际内容");
      event.target.setCustomValidity(""); change(parsed);
    } catch (error) { event.target.setCustomValidity(error instanceof Error ? error.message : "请输入有效 JSON"); }
  }} />;
}

export function ConfigurationSettings({ state }: { state: ConsoleState }) {
  const cwd = store.cwd;
  const resource = useResource(useCallback(() => native<ConfigSnapshot>("config/read", { includeLayers: true, ...(cwd ? { cwd } : {}) }), [cwd]));
  const requirements = useResource(useCallback(() => native("configRequirements/read", null), []));
  const [selected, setSelected] = useState("");
  const [draft, setDraft] = useState<Obj>({});
  const [reload, setReload] = useState(false);
  const [notice, setNotice] = useState("");
  const [fileOpen, setFileOpen] = useState(false);
  const [provider, setProvider] = useState("");
  const [formRevision, setFormRevision] = useState(0);
  const action = useAction();
  const data = resource.data;
  const layers = data?.layers || [];
  const layer = layers.find((row) => (configPath(row) || row.name.type) === selected) || layers.find((row) => row.name.type === "user") || layers[0];
  const own = layer?.config || {};
  const effective = data?.config || {};
  const writable = !!state.nativeProtocol?.configuration_editable && layer?.name.type === "user" && !layer.disabledReason;
  const disabled = !writable || resource.loading || !!resource.error || requirements.loading || !!requirements.error || action.busy;
  const req = requirements.data?.requirements;
  const dirty = Object.keys(draft).length > 0;
  useEffect(() => { setDraft({}); setNotice(""); setFileOpen(false); setProvider(""); }, [cwd, selected]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const read = (key: string) => key in draft ? draft[key] : configValue(own, key);
  const set = (key: string, value: unknown) => { setDraft((old) => ({ ...old, [key]: value })); setNotice(""); };
  const origin = (key: string) => {
    const source = data?.origins?.[key] || data?.origins?.[key.split(".")[0]];
    const value = configValue(effective, key);
    const display = value == null ? "运行时默认" : isRedacted(value) ? "已配置（内容隐藏）" : typeof value === "object" ? "已配置" : labels[String(value)] || String(value);
    return `当前生效：${display.length > 80 ? display.slice(0, 80) + "…" : display}${source ? ` · ${layerLabel(source)}` : ""}`;
  };
  const row = (key: string, label: string, control: ReactNode, description = "") => <SettingRow key={key} label={label} description={[description, origin(key)].filter(Boolean).join(" · ")}>{control}</SettingRow>;
  const select = (key: string, label: string, values: string[], allowed?: string[], fallback = "继承默认") => {
    const value = read(key) ?? "";
    return row(key, label, <select aria-label={label} value={value} onChange={(e) => set(key, e.target.value || null)}>
      <option value="">{fallback}</option>
      {[...new Set([...(value ? [value] : []), ...values])].map((v) => <option key={v} value={v} disabled={!values.includes(v) || !!allowed && !allowed.includes(v)}>{labels[v] || effortNames[v] || v}</option>)}
    </select>);
  };
  const text = (key: string, label: string, description = "", list?: string) => row(key, label, <input aria-label={label} list={list} value={isRedacted(read(key)) ? "" : read(key) ?? ""} placeholder={isRedacted(read(key)) ? "已配置；输入新值以替换" : "继承默认"} onChange={(e) => set(key, e.target.value || null)} />, description);
  const toggle = (key: string, label: string) => row(key, label, <select aria-label={label} value={read(key) == null ? "" : String(read(key))} onChange={(e) => set(key, e.target.value === "" ? null : e.target.value === "true")}>
    <option value="">继承默认</option><option value="true">启用</option><option value="false">停用</option>
  </select>);
  const lines = (key: string, label: string) => row(key, label, <textarea aria-label={label} rows={3} value={Array.isArray(read(key)) ? read(key).join("\n") : ""} placeholder="每行一项，留空继承默认" onChange={(e) => set(key, e.target.value.trim() ? e.target.value.split("\n") : null)} />);
  const efforts = [...new Set(state.models.flatMap((m) => m.supportedReasoningEfforts?.map((e) => e.reasoningEffort) || []))];
  const model = state.models.find((m) => m.model === (read("model") || effective.model));
  const modelEfforts = model?.supportedReasoningEfforts?.map((e) => e.reasoningEffort) || [];
  const approval = read("approval_policy");
  const approvalValue = approval && typeof approval === "object" ? "granular" : approval || "";
  const allowedApprovals = req?.allowedApprovalPolicies?.map((v: unknown) => typeof v === "string" ? v : "granular");
  const personalityDeprecated = String((state.nativeProtocol?.definitions.Personality as Obj)?.description || "").includes("Deprecated");
  const approvalOptions = [...(supportsLegacyUntrusted(state.nativeProtocol?.version) ? ["untrusted"] : []), "on-request", "never", "granular"];
  const reset = async () => { await resource.refresh(); setDraft({}); setFormRevision((v) => v + 1); setNotice(""); };
  const save = async () => {
    const normalized = { ...draft };
    for (const key of numbers) if (key in normalized) normalized[key] = positiveInteger(String(normalized[key] ?? ""));
    const edits = configEdits(normalized, own);
    if (!edits.length) { setDraft({}); return; }
    const result = await native("config/batchWrite", configWriteParams(layer!, edits, reload));
    // The write succeeded even if the subsequent read fails; never replay it.
    setDraft({});
    setNotice(result.status === "okOverridden" || result.overriddenMetadata ? "已保存；部分值被更高优先级配置覆盖，请查看当前生效值。" : reload ? "默认配置已保存，并已请求原生重载。会话设置不会因此全部切换。" : "默认配置已保存，用于新聊天。");
    await resource.refresh();
    await store.refreshRuntimeDefaults(cwd);
    await store.refreshSessions();
  };
  return <section className="capability-page configuration-settings"><h3>配置</h3>
    <p className="muted">配置新聊天的权限、网页访问和智能体回复。当前工作目录：{cwd || "默认目录"}</p>
    <div className="configuration-toolbar"><label>配置来源<select aria-label="配置来源" disabled={dirty || action.busy} value={layer ? configPath(layer) || layer.name.type : ""} onChange={(e) => setSelected(e.target.value)}>
      {!layers.length && <option value="">读取中…</option>}{layers.map((item, index) => <option key={index} value={configPath(item) || item.name.type}>{layerLabel(item)}{item.name.type !== "user" ? "（只读）" : ""}{item.name.type === "project" ? ` · ${configPath(item)}` : ""}</option>)}
    </select></label><Button variant="ghost" disabled={!layer || !configPath(layer)} onClick={() => setFileOpen(!fileOpen)}>{fileOpen ? "关闭配置文件" : "打开 config.toml ↗"}</Button></div>
    {layer && <p className="muted configuration-path">{configPath(layer) || layerLabel(layer)}{layer.disabledReason && ` · ${layer.disabledReason}`}</p>}
    {fileOpen && layer && <div className="configuration-file"><WorkspaceFileView target={{ path: configPath(layer) }} active /></div>}
    {!writable && !resource.loading && <p role="status">{!state.nativeProtocol?.configuration_editable ? "此运行实例的发布配置为只读。" : "原生接口只允许写入用户配置；此处可查看该层的值和来源。"}</p>}
    <form onSubmit={(event) => { event.preventDefault(); void action.run(save, ""); }}>
      <fieldset disabled={disabled} key={`${cwd}:${selected}:${layer?.version}:${formRevision}`}><legend>智能体默认设置</legend>
        <div className="desktop-settings-group">
          {row("approval_policy", "批准策略", <select aria-label="默认批准策略" value={approvalValue} onChange={(e) => set("approval_policy", e.target.value === "granular" ? { granular: defaultGranular } : e.target.value || null)}>
            <option value="">继承默认</option>{[...new Set([...(approvalValue ? [approvalValue] : []), ...approvalOptions])].map((v) => <option key={v} value={v} disabled={!approvalOptions.includes(v) || allowedApprovals && !allowedApprovals.includes(v)}>{labels[v] || v}</option>)}
          </select>)}
          {approvalValue === "granular" && Object.entries(granularLabels).map(([key, label]) => <SettingRow key={key} label={label}><input type="checkbox" role="switch" aria-label={label} checked={!!approval.granular?.[key]} onChange={(e) => set("approval_policy", { granular: { ...approval.granular, [key]: e.target.checked } })} /></SettingRow>)}
          {select("sandbox_mode", "沙盒设置", ["read-only", "workspace-write", "danger-full-access"], req?.allowedSandboxModes)}
          {effective.default_permissions && <p className="muted">当前使用命名权限配置 {effective.default_permissions}，沙盒模式可能不会生效。</p>}
          {select("web_search", "网页搜索", ["disabled", "cached", "indexed", "live"], req?.allowedWebSearchModes)}
          {select("model_verbosity", "输出详细程度", ["low", "medium", "high"], undefined, "模型默认")}
          {select("model_reasoning_summary", "推理摘要", ["auto", "concise", "detailed", "none"])}
        </div>
        <details className="configuration-advanced"><summary>模型与运行设置</summary><div className="desktop-settings-group">
          {text("model", "默认模型", "模型目录以运行实例提供的列表为准", "config-models")}
          <datalist id="config-models">{state.models.map((m) => <option key={m.model} value={m.model}>{m.displayName}</option>)}</datalist>
          {text("model_provider", "默认服务商", "填写原生服务商 ID", "config-providers")}
          <datalist id="config-providers">{Object.keys(effective.model_providers || {}).map((id) => <option key={id}>{id}</option>)}</datalist>
          {select("model_reasoning_effort", "默认推理强度", modelEfforts)}
          {select("plan_mode_reasoning_effort", "计划模式推理强度", modelEfforts)}
          {text("service_tier", "默认服务等级", "可用等级取决于模型和账号")}
          {!personalityDeprecated && select("personality", "交流风格", ["none", "friendly", "pragmatic"])}
          {select("approvals_reviewer", "审批方式", req?.allowedApprovalsReviewers || ["user", "auto_review"])}
          {numbers.map((key, index) => row(key, index ? "自动压缩阈值" : "上下文窗口", <input type="number" min="1" step="1" aria-label={index ? "自动压缩阈值" : "上下文窗口"} placeholder="模型默认" value={read(key) ?? ""} onChange={(e) => set(key, e.target.value)} />, "Token 数量"))}
        </div></details>
        <details className="configuration-advanced"><summary>沙箱与命令环境</summary><div className="desktop-settings-group">
          {text("default_permissions", "默认权限配置 ID", "使用已定义的命名权限配置；留空恢复沙箱模式")}
          {lines("sandbox_workspace_write.writable_roots", "额外可写目录")}
          {toggle("sandbox_workspace_write.network_access", "允许命令访问网络")}
          {toggle("sandbox_workspace_write.exclude_tmpdir_env_var", "排除 TMPDIR 临时目录")}
          {toggle("sandbox_workspace_write.exclude_slash_tmp", "排除 /tmp 目录")}
          {select("shell_environment_policy.inherit", "继承 Shell 环境", ["all", "core", "none"])}
          {lines("shell_environment_policy.include_only", "仅保留环境变量")}
          {lines("shell_environment_policy.exclude", "排除环境变量")}
          {row("shell_environment_policy.set", "设置环境变量", <JsonSetting label="设置环境变量" value={read("shell_environment_policy.set")} change={(v) => set("shell_environment_policy.set", v)} />, "JSON 对象；隐藏值不会自动回写")}
        </div></details>
        <details className="configuration-advanced"><summary>自定义指令</summary><div className="desktop-settings-group">
          {row("developer_instructions", "开发者指令", <textarea aria-label="开发者指令" rows={5} value={isRedacted(read("developer_instructions")) ? "" : read("developer_instructions") ?? ""} placeholder={isRedacted(read("developer_instructions")) ? "已有指令内容已隐藏；输入新内容以替换" : "继承默认"} onChange={(e) => set("developer_instructions", e.target.value || null)} />)}
          {text("model_instructions_file", "模型指令文件", "覆盖模型内置指令的文件路径")}
        </div></details>
        <details className="configuration-advanced"><summary>服务商配置</summary>
          <p className="muted">按字段保存到用户配置。密钥由环境变量引用，已有隐藏字段会保留。</p>
          <label className="configuration-provider">服务商 ID<input aria-label="编辑服务商 ID" list="config-providers" value={provider} disabled={dirty} onChange={(e) => setProvider(e.target.value)} placeholder="选择或填写服务商 ID" /></label>
          {provider && !/^[a-zA-Z0-9_-]+$/.test(provider) && <p role="alert">服务商 ID 请使用字母、数字、下划线或连字符。</p>}
          {/^[a-zA-Z0-9_-]+$/.test(provider) && <div className="desktop-settings-group" key={provider}>
            {text(`model_providers.${provider}.name`, "服务商名称")}
            {text(`model_providers.${provider}.base_url`, "API 地址")}
            {text(`model_providers.${provider}.env_key`, "密钥环境变量名")}
            {select(`model_providers.${provider}.wire_api`, "API 协议", ["responses"])}
            {toggle(`model_providers.${provider}.requires_openai_auth`, "使用 OpenAI 登录认证")}
          </div>}
        </details>
      </fieldset>
      <label className="native-checkbox"><input type="checkbox" checked={reload} disabled={disabled} onChange={(e) => setReload(e.target.checked)} />同时请求原生重载</label>
      <p className="muted">默认设置用于新聊天。重载只更新运行时允许的配置（如 MCP、插件）；已有聊天的权限、模型和回复设置请在“模型与权限”中单独修改。</p>
      <div className="native-options"><Button variant="primary" type="submit" disabled={disabled || !dirty}>{action.busy ? "保存中…" : "保存默认配置"}</Button>
        <Button type="button" variant="outline" disabled={action.busy || resource.loading} onClick={() => void action.run(reset, "")}>{dirty ? "放弃修改并重新读取" : "重新读取配置"}</Button></div>
    </form>
    <Feedback error={action.error || resource.error || requirements.error} status={notice || (resource.loading ? "读取配置中…" : "")} />
    <h4>模型功能</h4><div className="desktop-settings-group"><SettingRow label="可用推理强度" description="选择在模型控件中显示的档位，仅保存在此浏览器。当前正在使用的档位始终保留。">
      <details className="configuration-efforts"><summary>已选择 {efforts.filter((e) => !state.hiddenReasoningEfforts.includes(e)).length} 个</summary>
        {efforts.map((effort) => <label key={effort}><input type="checkbox" aria-label={`显示${effortNames[effort] || effort}推理强度`} checked={!state.hiddenReasoningEfforts.includes(effort)} onChange={(e) => {
          const hidden = e.target.checked ? state.hiddenReasoningEfforts.filter((v) => v !== effort) : [...state.hiddenReasoningEfforts, effort];
          void action.run(async () => { store.setHiddenReasoningEfforts(hidden); }, "");
        }} />{effortNames[effort] || effort}</label>)}
        {!efforts.length && <p>运行实例尚未提供推理档位。</p>}
      </details>
    </SettingRow></div>
    <details className="configuration-advanced"><summary>配置层与覆盖关系</summary><ul className="capability-list">{layers.map((item, index) => <li key={index}><strong>{layerLabel(item)}</strong><p className="configuration-path">{configPath(item) || "运行时提供"}</p>{item.disabledReason && <p>{item.disabledReason}</p>}</li>)}</ul><p className="muted">列表按优先级从高到低显示。恢复默认会删除所选用户层的值，由其他配置层或运行时决定。</p></details>
    <CompleteConfigSettings state={state} />
  </section>;
}
