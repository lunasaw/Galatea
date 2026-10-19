import { useCallback, useState } from "react";
import { native, type Obj } from "./native";
import { Field, Facts, Feedback, useAction, useResource } from "./capability-controls";
import { Button } from "./components/ui/button";
import { httpUrl, type Marketplace } from "./integrations-data";
import { integrationsChanged } from "./integrations";

export function PluginSkill({ marketplace, pluginId, name }: { marketplace: string; pluginId: string; name: string }) {
  const [contents, setContents] = useState(""); const action = useAction();
  return <div><Button variant="ghost" size="sm" disabled={action.busy} onClick={() => void action.run(async () => {
    const response = await native("plugin/skill/read", { remoteMarketplaceName: marketplace, remotePluginId: pluginId, skillName: name }); setContents(response.contents);
  }, "")}>阅读 Skill</Button>{contents && <pre className="whitespace-pre-wrap">{contents}</pre>}<Feedback error={action.error} /></div>;
}
export function MarketplaceSettings({ markets, editable }: { markets: Marketplace[]; editable: boolean }) {
  const [source, setSource] = useState(""); const [ref, setRef] = useState(""); const [paths, setPaths] = useState(""); const [remove, setRemove] = useState(""); const action = useAction();
  return <details><summary>管理插件市场</summary><form className="capability-page" onSubmit={(e) => { e.preventDefault(); void action.run(async () => {
    await native("marketplace/add", { source, ...(ref ? { refName: ref } : {}), ...(paths.trim() ? { sparsePaths: paths.split("\n").filter(Boolean) } : {}) }); integrationsChanged();
  }, "市场已添加"); }}><Field label="市场来源"><input value={source} onChange={(e) => setSource(e.target.value)} required /></Field><Field label="分支或引用（可选）"><input value={ref} onChange={(e) => setRef(e.target.value)} /></Field><Field label="只检出的目录（可选，每行一个）"><textarea value={paths} onChange={(e) => setPaths(e.target.value)} /></Field><Button disabled={!editable || action.busy || !source}>添加市场</Button></form>
    <ul className="capability-list">{markets.map((market) => <li key={market.name}><strong>{market.name}</strong><small>{market.path}</small><div className="native-options"><Button variant="outline" disabled={!editable || action.busy} onClick={() => void action.run(async () => { await native("marketplace/upgrade", { marketplaceName: market.name }); integrationsChanged(); }, "市场已更新")}>更新市场</Button><Button variant="ghost" disabled={!editable} onClick={() => setRemove(market.name)}>移除市场…</Button></div>
      {remove === market.name && <div><p>移除市场 {market.name}？</p><Button variant="destructive" disabled={action.busy} onClick={() => void action.run(async () => { await native("marketplace/remove", { marketplaceName: market.name }); setRemove(""); integrationsChanged(); }, "市场已移除")}>确认移除</Button><Button variant="ghost" onClick={() => setRemove("")}>取消</Button></div>}</li>)}</ul><Feedback error={action.error} status={action.status} />
  </details>;
}
export function PluginShares() {
  const resource = useResource(useCallback(() => native("plugin/share/list"), [])); const action = useAction();
  const [path, setPath] = useState(""); const [remoteId, setRemoteId] = useState(""); const [visibility, setVisibility] = useState("PRIVATE"); const [targets, setTargets] = useState<Obj[]>([]);
  const [remove, setRemove] = useState(""); const [result, setResult] = useState<Obj>();
  return <section className="capability-page"><h4>共享插件</h4><Button variant="outline" onClick={() => void resource.refresh()}>刷新共享列表</Button>
    <ul className="capability-list">{resource.data?.data?.map((row: Obj) => {
      const context = row.plugin.shareContext; const id = context?.remotePluginId; return <li key={id || row.plugin.id}><strong>{row.plugin.interface?.displayName || row.plugin.name}</strong><p>{row.localPluginPath || "尚未检出到本机"}</p><p>{context?.discoverability}</p>
        {httpUrl(context?.shareUrl) && <a href={httpUrl(context.shareUrl)} target="_blank" rel="noopener noreferrer">打开共享链接</a>}
        <div className="native-options"><Button variant="outline" disabled={action.busy || !id} onClick={() => void action.run(async () => { const r = await native("plugin/share/checkout", { remotePluginId: id }); setPath(r.pluginPath); setRemoteId(id); await resource.refresh(); }, "已检出到本机")}>检出到本机</Button>
          <Button variant="ghost" disabled={!row.localPluginPath} onClick={() => { setPath(row.localPluginPath); setRemoteId(id || ""); setVisibility(context?.discoverability || "PRIVATE"); setTargets((context?.sharePrincipals || []).filter((p: Obj) => p.role !== "owner").map(({ principalId, principalType, role }: Obj) => ({ principalId, principalType, role }))); }}>编辑共享设置</Button>
          <Button variant="ghost" disabled={!id} onClick={() => setRemove(id)}>删除共享…</Button></div>
        {remove === id && <div><p>删除此插件的共享发布？</p><Button variant="destructive" disabled={action.busy} onClick={() => void action.run(async () => { await native("plugin/share/delete", { remotePluginId: id }); setRemove(""); await resource.refresh(); }, "共享已删除")}>确认删除共享</Button><Button variant="ghost" onClick={() => setRemove("")}>取消</Button></div>}
      </li>;
    })}</ul>
    <form onSubmit={(e) => { e.preventDefault(); void action.run(async () => {
      setResult(await native("plugin/share/save", { pluginPath: path, ...(remoteId ? { remotePluginId: remoteId } : {}), discoverability: visibility, shareTargets: targets })); await resource.refresh();
    }, "共享设置已保存"); }}><Field label="本地插件目录"><input value={path} onChange={(e) => setPath(e.target.value)} required /></Field><Field label="已有共享插件 ID（新发布留空）"><input value={remoteId} onChange={(e) => setRemoteId(e.target.value)} /></Field>
      <Field label="可见范围"><select value={visibility} onChange={(e) => setVisibility(e.target.value)}><option value="PRIVATE">私有</option><option value="UNLISTED">不列出，可通过链接访问</option><option value="LISTED">列入工作区目录</option></select></Field>
      <h4>指定访问对象</h4>{targets.map((target, i) => <div className="native-grid" key={i}><Field label={`对象 ${i + 1} ID`}><input required value={target.principalId} onChange={(e) => setTargets(targets.map((t, j) => i === j ? { ...t, principalId: e.target.value } : t))} /></Field><Field label="对象类型"><select value={target.principalType} onChange={(e) => setTargets(targets.map((t, j) => i === j ? { ...t, principalType: e.target.value } : t))}>{["user", "group", "workspace"].map((v) => <option key={v}>{v}</option>)}</select></Field><Field label="权限"><select value={target.role} onChange={(e) => setTargets(targets.map((t, j) => i === j ? { ...t, role: e.target.value } : t))}><option value="reader">可阅读</option><option value="editor">可编辑</option></select></Field><Button type="button" variant="ghost" onClick={() => setTargets(targets.filter((_, j) => j !== i))}>移除此对象</Button></div>)}
      <Button type="button" variant="outline" onClick={() => setTargets([...targets, { principalId: "", principalType: "user", role: "reader" }])}>添加访问对象</Button>
      <Button disabled={action.busy || !path.trim()}>{remoteId ? "保存共享设置" : "发布共享插件"}</Button></form>
    {result && <><Facts value={result} />{httpUrl(result.shareUrl) && <a href={httpUrl(result.shareUrl)} target="_blank" rel="noopener noreferrer">打开发布结果</a>}</>}
    <Feedback error={resource.error || action.error} status={action.status} />
  </section>;
}
