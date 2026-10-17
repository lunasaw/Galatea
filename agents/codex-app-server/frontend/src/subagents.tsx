import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Bot, ChevronRight, LoaderCircle, RefreshCw } from "lucide-react";
import { native, type Obj } from "./native";
import type { ConsoleState } from "./store";
import type { Step } from "./types";
import { agentActivities, agentPath, agentStatus, agentTitle, mergeAgentTurns, subagentSources } from "./subagent-data";
import { StaticMarkdown as MarkdownContent } from "./components/assistant-ui/elements/markdown-text";
import { NativeToolActivity } from "./tool-ui";
import { WorkspaceFileScope } from "./workspace-file";
import { Button } from "./components/ui/button";
import "./subagents.css";

export function Subagents({ state, selectedId, onSelect }: {
  state: ConsoleState; selectedId: string; onSelect: (id: string) => void;
}) {
  const parentId = state.sessions.find(s => s.id === state.active)?.thread_id;
  const [threads, setThreads] = useState<Obj[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [pages, setPages] = useState(1);
  const [more, setMore] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const activities = agentActivities(state.snapshot?.steps);

  useEffect(() => {
    if (!parentId) { setLoading(false); return; }
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const rows = new Map<string, Obj>();
        let cursor: string | null = null;
        for (let page = 0; page < pages; page++) {
          const result: Obj = await native("thread/list", {
            ancestorThreadId: parentId, sourceKinds: subagentSources, limit: 50,
            sortKey: "created_at", sortDirection: "asc", ...(cursor ? { cursor } : {}),
          });
          if (disposed) return;
          for (const thread of result.data || []) rows.set(thread.id, thread);
          cursor = result.nextCursor;
          if (!cursor) break;
        }
        setThreads([...rows.values()]); setMore(!!cursor); setError("");
      } catch (error) { if (!disposed) setError(String(error)); }
      finally {
        if (!disposed) { setLoading(false); timer = setTimeout(load, 3000); }
      }
    };
    void load();
    return () => { disposed = true; clearTimeout(timer); };
  }, [parentId, pages, refresh]);

  const selected = threads.find(thread => thread.id === selectedId);
  if (selectedId && parentId) return <AgentConversation key={selectedId}
    thread={selected || { id: selectedId, agentPath: activities.get(selectedId)?.path }} parentId={parentId}
    activity={activities.get(selectedId)} onBack={() => onSelect("")} />;
  return <section className="subagents-panel" aria-label="当前聊天的子 Agent">
    <header className="subagents-list-header"><div><h3>当前聊天的子 Agent</h3><p>{threads.length ? `${threads.length} 个子 Agent` : "任务分工与执行进度"}</p></div>
      <Button variant="ghost" size="icon" aria-label="刷新子 Agent" onClick={() => setRefresh(v => v + 1)}><RefreshCw size={16} /></Button></header>
    {!parentId ? <p className="subagents-empty">先打开一个聊天。</p>
      : loading ? <p className="subagents-empty" role="status">正在读取子 Agent…</p>
      : !threads.length && !error ? <p className="subagents-empty">这个聊天还没有子 Agent。派出子任务后，进度会显示在这里。</p> : null}
    <div className="subagents-list">{threads.map(thread => {
      const activity = activities.get(thread.id);
      const status = agentStatus(thread, [], activity);
      return <button className="subagent-row" key={thread.id} onClick={() => onSelect(thread.id)} aria-label={`查看 ${agentTitle(thread)}`}>
        <AgentAvatar id={thread.id} /><span className="subagent-row-body"><strong>{agentTitle(thread)}</strong>
          <small>{thread.agentRole || thread.agentNickname || agentPath(thread)}</small>
          {(activity?.prompt || thread.preview) && <span className="subagent-task-preview">{activity?.prompt || thread.preview}</span>}
          <span className="subagent-status" data-running={status.running}>{status.running && <LoaderCircle size={12} className="animate-spin" />}{status.label}</span>
        </span><ChevronRight size={15} /></button>;
    })}</div>
    {more && <Button variant="ghost" onClick={() => setPages(p => p + 1)}>加载更多子 Agent</Button>}
    {error && <p className="subagents-error" role="alert">子 Agent 列表暂时无法更新：{error}</p>}
  </section>;
}

function AgentAvatar({ id }: { id: string }) {
  const hue = [...id].reduce((sum, c) => sum + c.charCodeAt(0), 0) % 4;
  return <span className="subagent-avatar" data-color={hue} aria-hidden><Bot size={19} /></span>;
}

function AgentConversation({ thread, parentId, activity, onBack }: { thread: Obj; parentId: string; activity?: Obj; onBack: () => void }) {
  const [metadata, setMetadata] = useState(thread);
  const [turns, setTurns] = useState<Obj[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [paging, setPaging] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [now, setNow] = useState(Date.now());
  const generation = useRef(0);
  const paged = useRef(false);
  const body = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const scrollRestore = useRef<{ height: number; top: number } | null>(null);
  useEffect(() => {
    const version = ++generation.current;
    setPaging(false);
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        // Metadata-only read does not resume the agent or switch the main chat.
        // Full turn pages include in-progress items from the native runtime.
        const [read, page] = await Promise.all([
          native("thread/read", { threadId: thread.id, includeTurns: false }),
          native("thread/turns/list", { threadId: thread.id, limit: 20, sortDirection: "desc", itemsView: "full" }),
        ]);
        if (version !== generation.current) return;
        setMetadata(read.thread);
        setTurns(previous => mergeAgentTurns(previous, [...(page.data || [])].reverse()));
        if (!paged.current) setCursor(page.nextCursor || null);
        setLoaded(true); setError("");
      } catch (error) { if (version === generation.current) setError(String(error)); }
      finally { if (version === generation.current) timer = setTimeout(load, 1500); }
    };
    void load();
    return () => { generation.current++; clearTimeout(timer); };
  }, [thread.id, refresh]);
  const status = agentStatus(metadata, turns, activity);
  useEffect(() => {
    if (!status.running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [status.running]);
  useEffect(() => {
    if (!body.current) return;
    const restore = scrollRestore.current;
    if (restore) {
      body.current.scrollTop = restore.top + body.current.scrollHeight - restore.height;
      scrollRestore.current = null;
    } else if (follow.current) body.current.scrollTop = body.current.scrollHeight;
  }, [turns]);
  const older = async () => {
    if (!cursor || paging) return;
    const version = generation.current;
    setPaging(true);
    try {
      const page = await native("thread/turns/list", { threadId: thread.id, cursor, limit: 20, sortDirection: "desc", itemsView: "full" });
      if (version !== generation.current) return;
      if (body.current) scrollRestore.current = { height: body.current.scrollHeight, top: body.current.scrollTop };
      paged.current = true;
      setTurns(previous => mergeAgentTurns([...(page.data || [])].reverse(), previous));
      setCursor(page.nextCursor || null); setError("");
    } catch (error) { if (version === generation.current) setError(String(error)); }
    finally { if (version === generation.current) setPaging(false); }
  };
  return <section className="subagent-conversation" aria-label={`${agentTitle(metadata)} 的会话`}>
    <header className="subagent-conversation-header"><Button variant="ghost" size="icon" aria-label="返回子 Agent 列表" onClick={onBack}><ArrowLeft size={17} /></Button>
      <AgentAvatar id={thread.id} /><div><h3>{agentTitle(metadata)}</h3><span>{metadata.agentNickname}{metadata.agentRole && ` · ${metadata.agentRole}`}</span></div>
      <Button variant="ghost" size="icon" aria-label="刷新子 Agent 会话" onClick={() => setRefresh(v => v + 1)}><RefreshCw size={15} /></Button>
    </header>
    <div className="subagent-conversation-status" role="status"><span className="subagent-status" data-running={status.running}>
      {status.running && <LoaderCircle className="animate-spin" size={12} />}{status.label}</span>
      <span>{elapsed(turns, now)}</span></div>
    <div ref={body} className="subagent-transcript" onScroll={event => {
      const node = event.currentTarget; follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 60;
    }}>
      <WorkspaceFileScope cwd={metadata.cwd || thread.cwd || ""}>
        {cursor && <Button className="subagent-load-earlier" variant="ghost" size="sm" disabled={paging} onClick={() => void older()}>{paging ? "正在加载…" : "加载更早的消息"}</Button>}
        {activity?.prompt && <details className="subagent-task"><summary>分配的任务</summary><MarkdownContent text={activity.prompt} /></details>}
        {!loaded && !error && <p role="status" className="subagents-empty">正在读取会话…</p>}
        {loaded && !turns.length && <p className="subagents-empty">尚无会话内容，等待子 Agent 开始工作。</p>}
        {turns.map(turn => <div className="subagent-turn" key={turn.id}>
          {(turn.items || []).map((item: Obj) => <AgentItem key={item.id} item={item} turn={turn} parentId={parentId} threadId={thread.id} />)}
          {turn.error && <p className="subagents-error" role="alert">{turn.error.message || "子 Agent 执行失败"}</p>}
        </div>)}
      </WorkspaceFileScope>
      {error && <p className="subagents-error" role="alert">{loaded ? "会话暂时无法更新，已保留当前内容。" : "暂时无法读取会话。"}{error}</p>}
    </div>
  </section>;
}

function elapsed(turns: Obj[], now: number) {
  if (!turns.length) return "";
  let ms = 0;
  for (const turn of turns) {
    if (typeof turn.durationMs === "number") ms += turn.durationMs;
    else if (turn.startedAt && (turn.completedAt || turn.status === "inProgress")) ms += (turn.completedAt ? turn.completedAt * 1000 : now) - turn.startedAt * 1000;
  }
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return seconds >= 60 ? `已处理 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : `已处理 ${seconds} 秒`;
}

function AgentItem({ item, turn, parentId, threadId }: { item: Obj; turn: Obj; parentId: string; threadId: string }) {
  if (item.type === "agentMessage") return <div className="subagent-message" data-phase={item.phase}><MarkdownContent text={item.text || ""} /></div>;
  if (item.type === "userMessage") {
    const text = (item.content || []).filter((p: Obj) => p.type === "text").map((p: Obj) => p.text).join("\n");
    return text ? <details className="subagent-task"><summary>收到任务</summary><MarkdownContent text={text} /></details> : null;
  }
  if (item.type === "reasoning") {
    const text = (item.summary || []).filter((part: unknown) => typeof part === "string").join("\n\n");
    return text ? <details className="subagent-reasoning"><summary>处理摘要</summary><MarkdownContent text={text} /></details> : null;
  }
  if (item.type === "subAgentActivity" && item.agentThreadId === parentId) return <p className="subagents-note">与主 Agent 交互</p>;
  const step: Step = { id: item.id, type: item.type, first_seq: "0", last_seq: "0", status: item.status || (turn.status === "inProgress" ? "inProgress" : "completed"), detail: item };
  return <NativeToolActivity step={step} running={turn.status === "inProgress"} threadId={threadId} />;
}
