import { useCallback, useState } from "react";
import { native, type Obj } from "./native";
import { allPages, Feedback, useAction, useResource } from "./capability-controls";
import { Button } from "./components/ui/button";
import { httpUrl } from "./integrations-data";
import type { ConsoleState } from "./store";

export function AppsPanel({ state }: { state: ConsoleState }) {
  const threadId = state.sessions.find((s) => s.id === state.active)?.thread_id; const context = threadId ? { threadId } : {};
  const catalog = useResource(useCallback(() => allPages("app/list", threadId ? { threadId } : {}), [threadId]));
  const installed = useResource(useCallback(() => native("app/installed", threadId ? { threadId } : {}), [threadId]));
  const [search, setSearch] = useState(""); const [onlyInstalled, setOnlyInstalled] = useState(false); const [detail, setDetail] = useState<Obj>(); const action = useAction();
  const byId = new Map<string, Obj>((installed.data?.apps || []).map((a: Obj) => [a.id, a]));
  const apps = new Map<string, Obj>((catalog.data || []).map((a) => [a.id, a]));
  for (const a of byId.values()) if (!apps.has(a.id)) apps.set(a.id, { id: a.id, name: a.runtimeName || a.id });
  return <section className="capability-page"><h3>Apps</h3><div className="integration-toolbar"><input aria-label="搜索 Apps" type="search" value={search} onChange={(e) => setSearch(e.target.value)} /><Button variant="outline" disabled={action.busy} onClick={() => void action.run(async () => {
    await native("app/installed", { ...context, forceRefresh: true }); await Promise.all([catalog.refresh(), installed.refresh()]);
  }, "Apps 已刷新")}>刷新</Button></div>
    <label className="native-checkbox"><input type="checkbox" checked={onlyInstalled} onChange={(e) => setOnlyInstalled(e.target.checked)} />仅显示已安装</label>
    <ul className="capability-list">{[...apps.values()].filter((app) => (!onlyInstalled || byId.has(app.id)) && `${app.name} ${app.description}`.toLowerCase().includes(search.toLowerCase())).map((app) => <li key={app.id}>
      <strong>{app.name}</strong><p>{app.description}</p><small>{byId.has(app.id) ? `已安装 · ${byId.get(app.id)!.enabled ? "已启用" : "已停用"} · ${byId.get(app.id)!.callable ? "可调用" : "当前不可调用"}` : "未安装"}</small>
      <div className="native-options"><Button variant="outline" disabled={action.busy} onClick={() => void action.run(async () => {
        const result = await native("app/read", { ...context, appIds: [app.id], includeTools: true });
        if (result.missingAppIds.includes(app.id)) throw new Error("该应用的详情当前不可用"); setDetail(result.apps[0]);
      }, "")}>查看详情</Button>{httpUrl(app.installUrl) && <a href={httpUrl(app.installUrl)} target="_blank" rel="noopener noreferrer">打开连接页面</a>}</div>
    </li>)}</ul>
    {detail && <section className="native-card"><h4>{detail.name}</h4><p>{detail.description}</p><small>{detail.pluginDisplayNames?.join(" · ")}</small>
      <ul className="capability-list">{detail.toolSummaries?.map((tool: Obj) => <li key={tool.name}><strong>{tool.title || tool.name}</strong><p>{tool.description}</p><small>{tool.isReadOnly ? "只读" : "可修改数据"} · {tool.isEnabled ? "已启用" : tool.disabledReason || "已停用"}</small></li>)}</ul></section>}
    <Feedback error={catalog.error || installed.error || action.error} status={action.status} />
  </section>;
}
