import { useCallback, useEffect, useState } from "react";
import { Button } from "./components/ui/button";
import { Feedback, useAction, useResource } from "./capability-controls";
import { SettingRow } from "./model-settings";
import { api, requestKey } from "./store";

type Profile = { enabled: boolean; connection: "current" | "custom"; baseUrl: string; model: string; quality: string; size: string; revision: string };
type Settings = Profile & { editable: boolean; keyConfigured: boolean; currentBaseUrl: string; currentKeyConfigured: boolean; scriptAvailable: boolean; sdkAvailable: boolean; skillPath: string | null; warning?: string };
type Check = { status: "blocked" | "unverified"; message: string; models: string[]; httpStatus?: number };
const fields = (data: Settings): Profile => ({ enabled: data.enabled, connection: data.connection, baseUrl: data.baseUrl, model: data.model, quality: data.quality, size: data.size, revision: data.revision });

export function ImageGenerationSettings() {
  const resource = useResource(useCallback(() => api<Settings>("/api/image-generation/settings"), []));
  const [draft, setDraft] = useState<Profile>(); const [key, setKey] = useState("");
  const [check, setCheck] = useState<Check>(); const [models, setModels] = useState<string[]>([]);
  const [warning, setWarning] = useState("");
  const action = useAction();
  useEffect(() => { if (resource.data) { setDraft(fields(resource.data)); setKey(""); setCheck(undefined); } }, [resource.data]);
  const update = (value: Partial<Profile>) => { setDraft((old) => old ? { ...old, ...value } : old); setCheck(undefined); if (value.connection || value.baseUrl !== undefined) setModels([]); };
  if (!draft || !resource.data) return <section className="capability-page"><h3>图像生成</h3><Feedback error={resource.error} status={resource.loading ? "读取图像设置…" : ""} />{!resource.loading && <Button onClick={() => void resource.refresh()}>重新读取</Button>}</section>;
  const saved = resource.data;
  const connectionChanged = draft.connection === "current" && !!saved.baseUrl && saved.baseUrl !== saved.currentBaseUrl;
  const dirty = JSON.stringify(draft) !== JSON.stringify(fields(saved)) || !!key || connectionChanged;
  const body = () => ({ ...draft, ...(key ? { apiKey: key } : {}) });
  const disabled = !saved.editable || action.busy || resource.loading;
  const currentUrl = saved.currentBaseUrl || "尚未提供";
  return <section className="capability-page image-generation-settings"><h3>图像生成</h3>
    <p className="muted">设置此实例的默认绘图模型与 API 连接，供各聊天生成或编辑图片。</p>
    <form onSubmit={(event) => { event.preventDefault(); void action.run(async () => {
      const result = await api<Settings>("/api/image-generation/settings", body(), requestKey());
      setKey(""); await resource.refresh();
      setWarning(result.warning || "");
      window.dispatchEvent(new Event("codex-native-resources-changed"));
    }, draft.enabled ? "API 绘图设置已保存并启用。在聊天中引用 $imagegen-api 即可使用。" : "API 绘图设置已保存，当前未启用。"); }}>
      <fieldset disabled={disabled}><legend>绘图连接</legend><div className="desktop-settings-group">
        <SettingRow label="启用 API 绘图" description="用于你发起的绘图请求；保存不会生成图片。内置绘图工具仍遵循原生账号限制。"><input type="checkbox" role="switch" aria-label="启用 API 绘图" checked={draft.enabled} onChange={(e) => update({ enabled: e.target.checked })} /></SettingRow>
        <SettingRow label="连接方式" description="保存时复用实例默认聊天服务的地址和密钥来源；聊天服务变更后需重新保存。"><select aria-label="图像连接方式" value={draft.connection} onChange={(e) => { setKey(""); update({ connection: e.target.value as Profile['connection'] }); }}><option value="current">复用默认聊天连接</option><option value="custom">独立 API 连接</option></select></SettingRow>
        <SettingRow label="API 地址" description={draft.connection === "current" ? "实例默认聊天服务的 API Base URL" : "支持 HTTP / HTTPS。填写以 /v1 结尾的基础地址，不含 /chat/completions。"}><input type="url" aria-label="图像 API 地址" readOnly={draft.connection === "current"} required value={draft.connection === "current" ? currentUrl : draft.baseUrl} placeholder="https://api.example.com/v1" onChange={(e) => { setKey(""); update({ baseUrl: e.target.value }); }} /></SettingRow>
        <SettingRow label="API Key" description={draft.connection === "current" ? "由服务端读取已有密钥，不在页面显示。" : "仅保存在服务端。留空保留同一地址的已存 Key，更换地址需重新填写。"}>{draft.connection === "current" ? <span>{saved.currentKeyConfigured ? "已配置 · 复用聊天 Key" : "没有可复用的 Key，请选择独立连接"}</span> : <input type="password" aria-label="图像 API Key" autoComplete="new-password" value={key} placeholder={saved.keyConfigured && draft.baseUrl === saved.baseUrl ? "已保存 · 输入以替换" : "输入此服务的 API Key"} onChange={(e) => { setKey(e.target.value); setCheck(undefined); }} />}</SettingRow>
      </div><h4>图像默认设置</h4><div className="desktop-settings-group">
        <SettingRow label="绘图模型" description="支持 GPT Image 系列；读取模型列表后可选择，也可以直接填写模型 ID。"><input aria-label="绘图模型" list="image-api-models" required value={draft.model} onChange={(e) => update({ model: e.target.value })} /><datalist id="image-api-models">{models.map((model) => <option key={model} value={model} />)}</datalist></SettingRow>
        <SettingRow label="图片质量" description="更高质量通常需要更长生成时间。"><select aria-label="图片质量" value={draft.quality} onChange={(e) => update({ quality: e.target.value })}>{Object.entries({ low: "低", medium: "中", high: "高", auto: "自动" }).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></SettingRow>
        <SettingRow label="图片尺寸"><select aria-label="图片尺寸" value={draft.size} onChange={(e) => update({ size: e.target.value })}><option value="auto">自动</option><option value="1024x1024">正方形 · 1024 × 1024</option><option value="1536x1024">横向 · 1536 × 1024</option><option value="1024x1536">纵向 · 1024 × 1536</option></select></SettingRow>
      </div></fieldset>
      {!saved.scriptAvailable && <p role="alert">当前实例未安装图像 API 技能脚本，安装后才能启用。</p>}
      {!saved.sdkAvailable && <p role="alert">服务端缺少绘图依赖，请更新安装本项目依赖。</p>}
      {connectionChanged && <p role="status">默认聊天服务已变更，请保存以同步绘图连接。</p>}
      {!saved.editable && <p className="muted">此运行实例的发布配置为只读。</p>}
      <div className="native-options"><Button type="button" variant="outline" disabled={disabled} onClick={() => void action.run(async () => { const result = await api<Check>("/api/image-generation/check", body(), requestKey()); setModels(result.models); setCheck(result); }, "")}>检查连接与模型</Button><Button variant="primary" type="submit" disabled={disabled || !dirty || draft.enabled && (!saved.scriptAvailable || !saved.sdkAvailable)}>{action.busy ? "处理中…" : draft.enabled ? "保存并启用 API 绘图" : "保存图像设置"}</Button><Button type="button" variant="ghost" disabled={action.busy} onClick={() => void action.run(async () => { setWarning(""); await resource.refresh(); }, "")}>重新读取</Button></div>
      <p className="muted">连接检查不会生成图片，也不能保证出图权限和额度。实际绘图按服务商规则计费。</p>
    </form>
    {check && <p role={check.status === "blocked" ? "alert" : "status"} className={check.status === "blocked" ? "text-destructive" : "muted"}>{check.message}{check.httpStatus ? `（HTTP ${check.httpStatus}）` : ""}</p>}
    <Feedback error={action.error || resource.error} status={action.status} />
    {warning && <p role="status" className="muted">{warning}</p>}
    {saved.enabled && <p className="muted">已启用的聊天入口：<code>$imagegen-api</code>。已打开的聊天若尚未发现技能，可在 Skills 中刷新后引用。</p>}
  </section>;
}
