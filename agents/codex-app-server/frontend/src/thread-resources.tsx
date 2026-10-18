import { useCallback, useState } from "react";
import { Button } from "./components/ui/button";
import { allPages, Facts, Feedback, Field, useAction, useResource } from "./capability-controls";
import { native, openNativeThread, type Obj } from "./native";
import { NativeOperation } from "./native-operation";
import { SettingRow } from "./model-settings";
import { consoleStore as store, type ConsoleState } from "./store";

export function ThreadResources({ state }: { state: ConsoleState }) {
  const session = state.sessions.find((s) => s.id === state.active);
  const threadId = session?.thread_id;
  const load = useCallback(async (): Promise<{ readState?: Obj; attachments: Obj[] }> => {
    if (!threadId) return { attachments: [] };
    const result = await native("thread/read", { threadId, includeTurns: false });
    return { readState: result.readState, attachments: await allPages("thread/attachment/list", { threadId }) };
  }, [threadId]);
  const resource = useResource(load, 5000); const action = useAction(); const [owners, setOwners] = useState<Obj[]>();
  const [source, setSource] = useState(""); const [shell, setShell] = useState(false);
  if (!threadId) return <section className="capability-page"><h3>聊天资源</h3><p>请先选择一个聊天。</p></section>;
  const readState = resource.data?.readState;
  return <section className="capability-page"><h3>聊天资源</h3><p className="muted">管理当前聊天的阅读状态、持久附件和输入预测。</p>
    <div className="settings-context"><span>当前聊天</span><span>{session?.name || "未命名聊天"}</span></div>
    <div className="desktop-settings-group"><SettingRow label="阅读状态" description={!readState ? "当前聊天暂不提供持久阅读状态。" : "同步到其他客户端；状态冲突时请刷新后重试。"}><span>{!readState ? "暂不可用" : readState.firstUnread ? "有未读内容" : "已读"}</span></SettingRow></div><div className="native-options">{["read", "unread"].map((type) => <Button key={type} variant="outline" disabled={action.busy || !readState?.revision} onClick={() => void action.run(async () => {
      if (!readState?.revision) return;
      await native("thread/readState/update", { threadId, expectedRevision: readState.revision, operation: { type } }); await resource.refresh(); await store.refreshSessions();
    }, "阅读状态已更新")}>{type === "read" ? "标记已读" : "标记未读"}</Button>)}</div>
    <h4>持久附件</h4><ul className="capability-list">{resource.data?.attachments.map((a) => <li key={a.id}><strong>{a.attachmentType} · {a.identityKey}</strong><Facts value={a.payload} /><div className="native-options"><Button variant="outline" disabled={action.busy} onClick={() => void action.run(async () => { setOwners(await allPages("thread/attachmentOwner/list", { attachmentType: a.attachmentType, identityKey: a.identityKey })); }, "所属聊天已读取")}>查找所属聊天</Button><Button variant="outline" disabled={action.busy} onClick={() => void action.run(async () => { await native("thread/attachment/remove", { threadId, attachmentType: a.attachmentType, identityKey: a.identityKey }); await resource.refresh(); }, "附件已移除")}>移除附件</Button></div></li>)}</ul>
    {!resource.loading && !resource.data?.attachments.length && <p className="muted">暂无持久附件。添加的引用会随聊天保留。</p>}
    <NativeOperation key={threadId} state={state} method="thread/attachment/add" title="添加持久附件" context={{ threadId }} onResult={resource.refresh} />
    {owners && <ul className="capability-list">{owners.map((o, i) => <li key={i}><Facts value={o} /><Button variant="outline" onClick={() => void action.run(() => openNativeThread(o.threadId || o.id), "")}>打开聊天</Button></li>)}</ul>}
    <details><summary>后续输入预测</summary><Field label="来源轮次"><select value={source} onChange={(e) => setSource(e.target.value)}><option value="">选择轮次</option>{state.snapshot?.turns?.map((t) => <option key={t.id} value={t.id}>{t.id}</option>)}</select></Field><Button disabled={!source || action.busy} onClick={() => void action.run(async () => { await native("thread/prediction/request", { threadId, sourceTurnId: source }); }, "预测请求已提交；结果在聊天状态中更新")}>请求输入预测</Button></details>
    <details><summary>在聊天内运行 Shell 命令</summary><p>原生聊天 Shell 保留管道和重定向语法，并以完全访问权限运行。命令及输出记录到当前聊天。</p><label className="native-checkbox"><input type="checkbox" checked={shell} onChange={(e) => setShell(e.target.checked)} />使用完全访问权限执行命令</label>{shell && <NativeOperation state={state} method="thread/shellCommand" title="执行聊天 Shell 命令" context={{ threadId }} description="提交后等待聊天中的执行结果；确认回执不代表执行完成。" />}</details>
    <Button variant="outline" disabled={resource.loading || action.busy} onClick={() => void resource.refresh()}>刷新聊天资源</Button><Feedback error={action.error || resource.error} status={action.status} />
  </section>;
}
