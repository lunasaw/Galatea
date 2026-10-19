import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "./components/ui/button";
import { Facts, Feedback, useAction, useResource } from "./capability-controls";
import { api, consoleStore as store, type ConsoleState } from "./store";
import { sharedMutation } from "./native-mutation";
import { native, type Obj } from "./native";
import { SchemaInput, schemaDefault } from "./schema-form";
import { schemaLabel } from "./schema-labels";
import { configPath, configWriteParams, isRedacted, layerLabel, structuredConfigEdits, type ConfigSnapshot } from "./configuration-data";

export const configGroups: Record<string, string[]> = {
  "配置集、权限与信任": ["profile", "profiles", "default_permissions", "permissions", "approval_policy", "approvals_reviewer", "sandbox_mode", "sandbox_workspace_write", "projects"],
  "模型与服务商": ["model", "model_provider", "model_providers", "review_model", "oss_provider", "openai_base_url", "chatgpt_base_url", "model_catalog_json", "responses_api_metadata", "service_tier"],
  "Agent、目标与记忆": ["agents", "orchestrator", "goals", "memories", "model_context_window", "model_auto_compact_token_limit", "model_auto_compact_token_limit_scope", "model_post_turn_compact_threshold_percent"],
  "Hooks、扩展与工具": ["hooks", "apps", "plugins", "skills", "marketplaces", "mcp_servers", "tools", "tool_suggest", "features", "auto_review", "tool_output_token_limit", "apps_mcp_product_sku"],
  "认证、隐私与遥测": ["cli_auth_credentials_store", "forced_login_method", "forced_chatgpt_workspace_id", "mcp_enterprise_managed_auth", "mcp_oauth_credentials_store", "mcp_oauth_callback_port", "mcp_oauth_callback_url", "mcp_optional_startup_grace_ms", "analytics", "feedback", "otel"],
  "指令、Shell 与项目": ["developer_instructions", "model_instructions_file", "compact_prompt", "project_doc_fallback_filenames", "project_doc_max_bytes", "project_root_markers", "shell_environment_policy", "allow_login_shell", "background_terminal_max_timeout", "include_apps_instructions", "include_collaboration_mode_instructions", "include_environment_context", "include_environment_context_time", "include_permissions_instructions"],
  "语音、浏览器与电脑操作": ["audio", "realtime", "browser_use", "computer_use", "experimental_realtime_start_instructions", "experimental_realtime_webrtc_call_base_url", "experimental_realtime_ws_backend_prompt", "experimental_realtime_ws_base_url", "experimental_realtime_ws_model", "experimental_realtime_ws_startup_context"],
};
const startup = new Set(["allow_symlinked_codex_home", "experimental_thread_store", "log_dir", "sqlite_home", "thread_unload_delay_secs"]);
const terminal = new Set(["tui", "file_opener", "disable_paste_burst", "notice", "check_for_update_on_startup", "hide_agent_reasoning", "show_raw_agent_reasoning"]);

export function CompleteConfigSettings({ state, only }: { state: ConsoleState; only?: string[] }) {
  const cwd = store.cwd;
  const schema = useResource(useCallback(() => api<Obj>("/api/native/config-schema"), []));
  const config = useResource(useCallback(() => native<ConfigSnapshot>("config/read", { includeLayers: true, ...(cwd ? { cwd } : {}) }), [cwd]));
  const [draft, setDraft] = useState<Obj>({}); const [selected, setSelected] = useState("");
  const [search, setSearch] = useState(""); const [group, setGroup] = useState(""); const [reload, setReload] = useState(false);
  const [saved, setSaved] = useState(""); const action = useAction();
  const layers = config.data?.layers || [];
  const layer = layers.find((l) => (configPath(l) || l.name.type) === selected) || layers.find((l) => l.name.type === "user") || layers[0];
  const baseline = useRef<typeof layer | undefined>(undefined);
  const dirty = Object.keys(draft).length > 0;
  const editLayer = dirty ? baseline.current || layer : layer;
  const own = editLayer?.config || {};
  const clearDraft = () => { baseline.current = undefined; setDraft({}); };
  const change = (key: string, value: any) => {
    if (!baseline.current && layer) baseline.current = structuredClone(layer);
    setDraft((d) => ({ ...d, [key]: value }));
  };
  const writable = state.nativeProtocol?.configuration_editable && layer?.name.type === "user" && !layer.disabledReason;
  useEffect(() => { clearDraft(); setSaved(""); }, [cwd, selected]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const properties = schema.data?.properties || {};
  const keys = Object.keys(properties).filter((key) => (!only || only.includes(key)) && (!group || (configGroups[group] || []).includes(key))
    && `${key} ${properties[key].description || ""}`.toLowerCase().includes(search.toLowerCase()));
  return <section className="complete-config-settings"><h4>{only ? "相关原生配置" : "全部原生设置"}</h4>
    <p className="muted">{Object.keys(properties).length} 个配置域，包含嵌套字段、命名条目和继承。用户层用于新聊天；重载仅影响原生允许在线更新的内容。原生会校验组织限制和字段组合。</p>
    <div className="native-options"><label>配置层<select aria-label="完整设置配置层" disabled={dirty || action.busy} value={layer ? configPath(layer) || layer.name.type : ""} onChange={(e) => setSelected(e.target.value)}>{layers.map((l, i) => <option key={i} value={configPath(l) || l.name.type}>{layerLabel(l)} · {configPath(l)}</option>)}</select></label>
      {!only && <label>设置分类<select aria-label="设置分类" value={group} onChange={(e) => setGroup(e.target.value)}><option value="">所有配置域</option>{Object.keys(configGroups).map((g) => <option key={g}>{g}</option>)}</select></label>}
      <input aria-label="搜索原生设置" placeholder="搜索配置名称或说明" value={search} onChange={(e) => setSearch(e.target.value)} /></div>
    <form onSubmit={(e) => { e.preventDefault(); void action.run(async () => {
      const updated = { ...own };
      for (const [key, value] of Object.entries(draft)) { if (value === undefined) delete updated[key]; else updated[key] = value; }
      const edits = structuredConfigEdits(own, updated);
      if (!edits.length) { clearDraft(); return; }
      const result = await sharedMutation(`config:${configPath(editLayer!)}`).run("config/batchWrite", configWriteParams(editLayer!, edits, reload));
      clearDraft(); setSaved(result.status === "okOverridden" ? "已保存；更高优先级配置覆盖了部分设置，请查看生效来源。" : "原生配置已保存。");
      await config.refresh(); await store.refreshRuntimeDefaults(cwd);
    }, ""); }}>
      <fieldset disabled={!writable || config.loading || schema.loading || !!config.error || !!schema.error || action.busy}>
        {keys.map((key) => {
          const included = key in draft ? draft[key] !== undefined : Object.hasOwn(own, key);
          const value = key in draft ? draft[key] : own[key]; const source = config.data?.origins?.[key];
          const legacy = properties[key]["x-native-write-status"] === "legacy-read-only";
          return <details className="configuration-advanced" key={`${key}:${editLayer?.version}`}><summary><span className="settings-disclosure-label">{schemaLabel(key) !== key && <span>{schemaLabel(key)}</span>}<span className={schemaLabel(key) !== key ? "settings-field-key" : undefined}>{key}{key in draft ? " · 已修改" : included ? " · 已配置" : " · 继承"}</span></span></summary>
            <p className="muted">{properties[key].description}</p>
            {startup.has(key) && <p>宿主启动设置：保存后需要重新启动原生进程。</p>}
            {terminal.has(key) && <p>此配置影响原生 CLI/TUI；Web 界面使用浏览器交互。</p>}
            {properties[key].deprecated && <p>此字段已被原生弃用。</p>}
            <details><summary>当前生效值{source ? ` · ${layerLabel(source)}` : ""}</summary><Facts value={config.data?.config?.[key]} /></details>
            <label className="native-checkbox"><input type="checkbox" disabled={legacy && !included} aria-label={`配置 ${key}`} checked={included} onChange={(e) => change(key, e.target.checked ? schemaDefault(properties[key], schema.data) : undefined)} />在此层配置（取消以恢复继承）</label>
            {included && <fieldset disabled={legacy}><SchemaInput schema={properties[key]} root={schema.data} value={value} change={(v) => change(key, v)} label={key} /></fieldset>}
            {isRedacted(value) && <p className="muted">未修改的隐藏字段会保留；替换数组或整个条目时须重新填写其中的隐藏字段。</p>}
          </details>;
        })}
      </fieldset>
      {!writable && <p className="muted">当前配置层只读；原生写入接口只支持可编辑的用户层。</p>}
      <label className="native-checkbox"><input type="checkbox" disabled={!writable || action.busy} checked={reload} onChange={(e) => setReload(e.target.checked)} />保存后请求原生重载</label>
      <div className="native-options"><Button type="submit" disabled={!writable || !dirty || action.busy || config.loading}>{action.busy ? "保存中…" : "保存原生设置"}</Button><Button type="button" variant="outline" disabled={action.busy} onClick={() => { clearDraft(); setSaved(""); void config.refresh(); }}>放弃修改并刷新</Button></div>
    </form>
    <Feedback error={action.error || config.error || schema.error} status={saved} />
  </section>;
}
