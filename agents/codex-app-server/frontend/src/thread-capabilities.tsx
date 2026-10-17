import { useCallback, useEffect, useRef, useState } from "react";
import { sharedMutation } from "./native-mutation";
import { Button } from "./components/ui/button";
import { consoleStore as store, requestKey, type ConsoleState } from "./store";
import { native, openNativeThread, type Obj } from "./native";
import { allPages, Field, Feedback, useAction, useResource } from "./capability-controls";
import { textInput, tokenBudget } from "./conversation-capabilities";
import { ChevronDown, ChevronUp, Target, ListOrdered } from "lucide-react";

const goalStatuses: Record<string, string> = { active: "进行中", paused: "已暂停", blocked: "受阻", usageLimited: "额度受限", budgetLimited: "预算已用尽", complete: "已完成" };

export function GoalCard({ threadId, revision, expanded, onExpandedChange }: {
  threadId?: string; revision: number; expanded: boolean; onExpandedChange: (expanded: boolean) => void;
}) {
  const load = useCallback(() => threadId ? native("thread/goal/get", { threadId }) : Promise.resolve({ goal: null }), [threadId, revision]);
  const resource = useResource(load, threadId ? 5000 : 0);
  const action = useAction();
  const goal = resource.data?.goal;
  const [objective, setObjective] = useState("");
  const [budget, setBudget] = useState("");
  const [editing, setEditing] = useState(false);
  const [clearing, setClearing] = useState(false);
  useEffect(() => {
    if (!editing && resource.data) {
      setObjective(goal?.objective || "");
      setBudget(goal?.tokenBudget == null ? "" : String(goal.tokenBudget));
    }
  }, [resource.data, editing]);
  if (!goal) return resource.error && !/goals feature is disabled/i.test(resource.error) ? <Feedback error={resource.error} /> : null;
  const change = (method: string, params: Obj = {}) => action.run(async () => {
    if (!threadId) return;
    await native(method, { threadId, ...params });
    setEditing(false); setClearing(false); await resource.refresh(); await store.refreshSessions();
  });
  return <section className="composer-goal-card" aria-label="当前聊天的目标">
    <div className="composer-card-heading">
      <button type="button" aria-expanded={expanded} onClick={() => onExpandedChange(!expanded)}>
        <Target size={16} /><strong>目标</strong>{goal && <span>{goalStatuses[goal.status] || goal.status}</span>}
        {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
      </button>
    </div>
    {goal && !expanded && <p className="composer-goal-objective">{goal.objective}</p>}
    {goal && <div className="composer-goal-usage"><span>{goal.tokensUsed.toLocaleString()} {goal.tokenBudget != null && `/ ${goal.tokenBudget.toLocaleString()}`} Token</span>
      <span>{Math.floor(goal.timeUsedSeconds / 60)} 分 {goal.timeUsedSeconds % 60} 秒</span></div>}
    {expanded && <div className="composer-card-body capability-page">
      <form onSubmit={(event) => { event.preventDefault(); void action.run(async () => {
        const parsedBudget = tokenBudget(budget);
        await native("thread/goal/set", { threadId, objective: objective.trim(), tokenBudget: parsedBudget });
        setEditing(false); await resource.refresh(); await store.refreshSessions();
      }, "目标已保存"); }}>
        <Field label="要持续追求的目标"><textarea required rows={3} value={objective} onChange={(event) => { setEditing(true); setObjective(event.target.value); }} /></Field>
        <Field label="Token 预算（可选）"><input type="number" min="1" step="1" value={budget} onChange={(event) => { setEditing(true); setBudget(event.target.value); }} placeholder="不设预算" /></Field>
        <div className="native-options"><Button size="sm" disabled={action.busy || !objective.trim()}>保存目标</Button>
          {goal && <Button type="button" variant="ghost" size="sm" onClick={() => { setEditing(false); setObjective(goal.objective); setBudget(goal.tokenBudget == null ? "" : String(goal.tokenBudget)); onExpandedChange(false); }}>收起</Button>}</div>
      </form>
      {goal && <div className="native-options">
        <Button type="button" variant="outline" size="sm" disabled={action.busy}
          onClick={() => void change("thread/goal/set", { status: goal.status === "active" ? "paused" : "active" })}>{goal.status === "active" ? "暂停目标" : goal.status === "complete" ? "重新开始目标" : "继续目标"}</Button>
        <Button type="button" variant="ghost" size="sm" disabled={action.busy} onClick={() => setClearing(true)}>清除目标…</Button>
        {clearing && <><span>清除当前聊天的目标？</span><Button type="button" variant="destructive" size="sm" disabled={action.busy} onClick={() => void change("thread/goal/clear")}>确认清除</Button><Button type="button" variant="ghost" size="sm" onClick={() => setClearing(false)}>取消</Button></>}
      </div>}
    </div>}
    <Feedback error={resource.error || action.error} status={action.status} />
  </section>;
}
export function QueueCard({ threadId, expanded, onExpandedChange }: {
  threadId?: string; expanded: boolean; onExpandedChange: (expanded: boolean) => void;
}) {
  const load = useCallback(() => threadId ? allPages("thread/queue/list", { threadId }) : Promise.resolve([]), [threadId]);
  const resource = useResource(load, 3000); const action = useAction();
  const [text, setText] = useState(""); const [editing, setEditing] = useState<Obj>();
  const [draft, setDraft] = useState("");
  const submission = sharedMutation(`queue:${threadId}`);
  const rows = resource.data || [];
  if (!expanded && !rows.length) return resource.error ? <Feedback error={resource.error} /> : null;
  const mutate = (method: string, params: Obj) => action.run(async () => {
    await native(method, { threadId, ...params }); await resource.refresh(); await store.refreshSessions(); store.schedule();
  });
  const reorder = (index: number, offset: number) => {
    const ids = rows.map((row) => row.id); [ids[index], ids[index + offset]] = [ids[index + offset], ids[index]];
    void mutate("thread/queue/reorder", { queuedSubmissionIds: ids });
  };
  return <section className="composer-goal-card" aria-label="当前聊天的输入队列">
    <div className="composer-card-heading"><button type="button" aria-expanded={expanded} onClick={() => onExpandedChange(!expanded)}>
      <ListOrdered size={16} /><strong>待发送消息</strong><span>{rows.length}</span>{expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
    </button></div>
    {!expanded && rows[0] && <p className="composer-goal-objective">{rows[0].input.filter((p: Obj) => p.type === "text").map((p: Obj) => p.text).join("\n")}</p>}
    {expanded && <div className="composer-card-body capability-page">
    {!threadId ? <p className="muted">先打开一个聊天，再添加待发送消息。</p> : <>
    <p className="muted">消息由运行时在聊天空闲时依次发送。</p>
    <form onSubmit={(e) => { e.preventDefault(); void action.run(async () => {
      await submission.run("thread/queue/add", { threadId, clientUserMessageId: requestKey(), input: textInput(text) }, JSON.stringify([threadId, text]));
      setText((current) => current === text ? "" : current); await resource.refresh();
    }, "已加入队列"); }}><Field label="排队输入"><textarea value={text} disabled={action.busy} onChange={(e) => setText(e.target.value)} required /></Field>
      <Button disabled={action.busy || !text.trim()}>加入队列</Button></form>
    <ol className="capability-list">{rows.map((row, index) => <li key={row.id}>
      {editing?.id === row.id ? <form onSubmit={(e) => { e.preventDefault(); void action.run(async () => {
        const other = row.input.filter((part: Obj) => part.type !== "text");
        await native("thread/queue/update", { threadId, queuedSubmissionId: row.id, input: [...textInput(draft), ...other] });
        setEditing(undefined); await resource.refresh();
      }); }}><Field label="编辑排队消息"><textarea value={draft} onChange={(e) => setDraft(e.target.value)} /></Field>
        <Button disabled={action.busy || !draft.trim()}>保存消息</Button><Button type="button" variant="ghost" onClick={() => setEditing(undefined)}>取消</Button></form>
        : <><p className="whitespace-pre-wrap">{row.input.filter((p: Obj) => p.type === "text").map((p: Obj) => p.text).join("\n")}</p>
          {row.input.filter((p: Obj) => p.type !== "text").map((p: Obj, i: number) => <small key={i}>{p.name || p.path || p.type}</small>)}
          <div className="native-options"><Button type="button" size="sm" disabled={action.busy} onClick={() => void mutate("thread/queue/start", { queuedSubmissionId: row.id })}>启动</Button>
            <Button type="button" size="sm" variant="ghost" disabled={action.busy} onClick={() => { setEditing(row); setDraft(row.input.filter((p: Obj) => p.type === "text").map((p: Obj) => p.text).join("\n")); }}>编辑</Button>
            <Button type="button" size="sm" variant="ghost" disabled={action.busy || index === 0} onClick={() => reorder(index, -1)}>上移</Button>
            <Button type="button" size="sm" variant="ghost" disabled={action.busy || index === rows.length - 1} onClick={() => reorder(index, 1)}>下移</Button>
            <Button type="button" size="sm" variant="ghost" disabled={action.busy} onClick={() => void mutate("thread/queue/delete", { queuedSubmissionId: row.id })}>删除</Button></div></>}
    </li>)}</ol>
    {resource.data && !rows.length && <p className="muted">队列为空</p>}
    </>}
    <Feedback error={resource.error || action.error} status={action.status} /></div>}
  </section>;
}

export function BackgroundTerminals({ threadId }: { threadId: string }) {
  const load = useCallback(() => allPages("thread/backgroundTerminals/list", { threadId }), [threadId]);
  const resource = useResource(load, 3000); const action = useAction();
  return <section className="capability-page"><h3>Agent 后台终端</h3>
    <ul className="capability-list">{resource.data?.map((row) => <li key={row.processId}><code>{row.command}</code><p className="muted">{row.cwd}</p>
      <p>PID {row.osPid ?? "未知"} · CPU {row.cpuPercent == null ? "未知" : `${row.cpuPercent}%`} · 内存 {row.rssKb == null ? "未知" : `${(row.rssKb / 1024).toFixed(1)} MB`}</p>
      <Button variant="outline" size="sm" disabled={action.busy} onClick={() => void action.run(async () => {
        await native("thread/backgroundTerminals/terminate", { threadId, processId: row.processId }); await resource.refresh();
      })}>停止进程</Button></li>)}</ul>
    {resource.data?.length === 0 && <p className="muted">没有后台终端</p>}
    <Button variant="outline" disabled={action.busy || !resource.data?.length} onClick={() => void action.run(async () => {
      await native("thread/backgroundTerminals/clean", { threadId }); await resource.refresh();
    })}>清理全部后台终端</Button><Feedback error={resource.error || action.error} status={action.status} />
  </section>;
}

export function SearchPanel() {
  const [query, setQuery] = useState(""); const [archived, setArchived] = useState(false);
  const [rows, setRows] = useState<Obj[]>([]); const [hits, setHits] = useState<Obj[]>([]);
  const [selected, setSelected] = useState<Obj>(); const [searched, setSearched] = useState("");
  const action = useAction();
  return <section className="capability-page"><h3>搜索会话正文</h3>
    <form onSubmit={(e) => { e.preventDefault(); void action.run(async () => {
      setSelected(undefined); setHits([]); setRows(await allPages("thread/search", { searchTerm: query, archived })); setSearched(query);
    }, "搜索完成"); }}><Field label="搜索正文"><input type="search" required value={query} onChange={(e) => setQuery(e.target.value)} /></Field>
      <label className="native-checkbox"><input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />搜索已归档会话</label>
      <Button disabled={action.busy || !query.trim()}>搜索</Button></form>
    <ul className="capability-list">{rows.map((row) => <li key={row.thread.id}>
      <button className="search-result" onClick={() => void action.run(async () => {
        setSelected(row.thread); setHits(await allPages("thread/searchOccurrences", { threadId: row.thread.id, searchTerm: searched }));
      }, "已读取命中位置")}><strong>{row.thread.name || row.thread.preview || row.thread.id}</strong><span>{row.snippet}</span></button>
    </li>)}</ul>
    {selected && <section><h4>{selected.name || "命中位置"}</h4><ul className="capability-list">{hits.map((hit, index) => <li key={index}>
      <p>{hit.snippet.slice(0, hit.snippetMatchRange.start)}<mark>{hit.snippet.slice(hit.snippetMatchRange.start, hit.snippetMatchRange.end)}</mark>{hit.snippet.slice(hit.snippetMatchRange.end)}</p>
      <small>轮次 {hit.turnId} · 消息 {hit.itemId}</small>
      <Button size="sm" variant="ghost" onClick={() => void action.run(async () => {
        await openNativeThread(selected.id);
        await store.locateItem(hit.itemId);
        window.dispatchEvent(new CustomEvent("codex-locate-message", { detail: { threadId: selected.id, turnId: hit.turnId, itemId: hit.itemId, cursor: hit.turnCursor } }));
      }, "已打开会话")}>打开会话并定位</Button>
    </li>)}</ul></section>}
    {searched && !rows.length && <p>没有找到匹配的会话</p>}<Feedback error={action.error} status={action.status} />
  </section>;
}

export function ThreadCapabilityPanel({ tab, state }: { tab: string; state: ConsoleState }) {
  const threadId = state.sessions.find((s) => s.id === state.active)?.thread_id;
  if (tab === "search") return <SearchPanel />;
  if (!threadId) return <p className="muted">先打开一个会话。</p>;
  return <div key={threadId}><BackgroundTerminals threadId={threadId} /></div>;
}
