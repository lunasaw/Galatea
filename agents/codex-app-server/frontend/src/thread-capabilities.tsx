import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { goalReader } from "./goal-reader";
import { sharedMutation } from "./native-mutation";
import { Button } from "./components/ui/button";
import { TooltipIconButton } from "./components/assistant-ui/elements/tooltip-icon-button";
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from "./components/ui/dropdown-menu";
import { consoleStore as store, type ConsoleState } from "./store";
import { native, openNativeThread, type Obj } from "./native";
import { allPages, Field, Feedback, useAction, useResource } from "./capability-controls";
import { textInput, tokenBudget } from "./conversation-capabilities";
import { ChevronDown, ChevronUp, Target, CircleDashed, MoreHorizontal, Trash2, X } from "lucide-react";

const goalStatuses: Record<string, string> = { active: "进行中", paused: "已暂停", blocked: "受阻", usageLimited: "额度受限", budgetLimited: "预算已用尽", complete: "已完成" };

export function GoalCard({ threadId, revision, expanded, onExpandedChange }: {
  threadId?: string; revision: number; expanded: boolean; onExpandedChange: (expanded: boolean) => void;
}) {
  const load = useMemo(() => goalReader(() => threadId
    ? native<{ goal: any }>("thread/goal/get", { threadId })
    : Promise.resolve({ goal: null })), [threadId, revision]);
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
        <div className="native-options"><Button variant="primary" size="sm" disabled={action.busy || !objective.trim()}>保存目标</Button>
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
export function QueueCard({ threadId, revision, canStart }: {
  threadId?: string; revision: number; canStart: boolean;
}) {
  const load = useCallback(() => threadId ? allPages("thread/queue/list", { threadId }) : Promise.resolve([]), [threadId]);
  const resource = useResource(load, threadId ? 3000 : 0);
  const action = useAction();
  const root = useRef<HTMLElement>(null);
  const previousRevision = useRef(revision);
  const [editing, setEditing] = useState<string>();
  const [draft, setDraft] = useState("");
  const [requested, setRequested] = useState(false);
  const rows = resource.data || [];
  useEffect(() => {
    if (previousRevision.current === revision) return;
    previousRevision.current = revision;
    void resource.refresh();
  }, [revision, resource.refresh]);
  useEffect(() => {
    const show = (event: Event) => {
      if ((event as CustomEvent).detail !== "queue") return;
      setRequested(true);
      void resource.refresh();
      requestAnimationFrame(() => { root.current?.scrollIntoView({ block: "nearest" }); root.current?.focus(); });
    };
    window.addEventListener("codex-composer-panel", show);
    return () => window.removeEventListener("codex-composer-panel", show);
  }, [resource.refresh]);
  const mutate = (method: string, params: Obj) => action.run(async () => {
    await sharedMutation(`queue-edit:${threadId}`).run(method, { threadId, ...params });
    setEditing(undefined);
    await resource.refresh();
    if (method === "thread/queue/start") { await store.refreshSessions(); store.schedule(); }
  }, "");
  const reorder = (index: number, offset: number) => {
    const ids = rows.map((row) => row.id); [ids[index], ids[index + offset]] = [ids[index + offset], ids[index]];
    void mutate("thread/queue/reorder", { queuedSubmissionIds: ids });
  };
  if (!rows.length && !resource.error && !action.error && !requested) return null;
  return <section ref={root} tabIndex={-1} className="composer-queue" aria-label="待发送消息">
    <ol className="composer-queue-list">{rows.map((row, index) => {
      const text = row.input.filter((part: Obj) => part.type === "text").map((part: Obj) => part.text).join("\n");
      const other = row.input.filter((part: Obj) => part.type !== "text");
      return <li key={row.id} className="composer-queue-item" data-queued-id={row.id}>
        <div className="composer-queue-row">
          <CircleDashed size={15} className="composer-queue-indicator" aria-hidden="true" />
          <button type="button" className="composer-queue-preview" title={text || "附件消息"}
            aria-label={`编辑待发送消息：${text || "附件消息"}`} disabled={action.busy}
            onClick={() => { setEditing(row.id); setDraft(text); }}>
            <span>{text || "附件消息"}</span>
            {!!other.length && <small>{other.map((part: Obj) => part.name || (part.type === "image" ? "图片" : part.type === "audio" ? "音频" : part.path || part.type)).join(" · ")}</small>}
          </button>
          <DropdownMenu><DropdownMenuTrigger asChild><button type="button" className="composer-queue-more" aria-label="待发送消息更多操作" disabled={action.busy}><MoreHorizontal size={16} /></button></DropdownMenuTrigger>
            <DropdownMenuContent side="top" align="end">
              <DropdownMenuItem disabled={!canStart} onSelect={() => void mutate("thread/queue/start", { queuedSubmissionId: row.id })}>立即发送</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => { setEditing(row.id); setDraft(text); }}>编辑</DropdownMenuItem>
              <DropdownMenuItem disabled={index === 0} onSelect={() => reorder(index, -1)}>上移</DropdownMenuItem>
              <DropdownMenuItem disabled={index === rows.length - 1} onSelect={() => reorder(index, 1)}>下移</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <TooltipIconButton type="button" tooltip="删除待发送消息" aria-label="删除待发送消息" className="composer-queue-delete"
            disabled={action.busy} onClick={() => void mutate("thread/queue/delete", { queuedSubmissionId: row.id })}><Trash2 size={15} /></TooltipIconButton>
        </div>
        {editing === row.id && <form className="composer-queue-edit" onSubmit={(event) => {
          event.preventDefault();
          void mutate("thread/queue/update", { queuedSubmissionId: row.id, input: [...(draft.trim() ? textInput(draft) : []), ...other] });
        }}>
          <textarea aria-label="编辑待发送消息" autoFocus rows={2} value={draft} disabled={action.busy} onChange={(event) => setDraft(event.target.value)} />
          <div><Button type="button" size="sm" variant="ghost" onClick={() => setEditing(undefined)}>取消</Button>
            <Button variant="primary" size="sm" disabled={action.busy || (!draft.trim() && !other.length)}>保存</Button></div>
        </form>}
      </li>;
    })}</ol>
    {!rows.length && requested && <div className="composer-queue-empty"><span>没有待发送消息</span><button type="button" aria-label="关闭队列提示" onClick={() => setRequested(false)}><X size={14} /></button></div>}
    {(resource.error || action.error) && <div className="composer-queue-error"><Feedback error={resource.error || action.error} /><button type="button" onClick={() => void resource.refresh()}>刷新</button></div>}
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
