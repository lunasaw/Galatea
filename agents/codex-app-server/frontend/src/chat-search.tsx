import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { FileText, Folder, MessageSquare, Search, SquarePen, ArrowLeft } from "lucide-react";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "./components/ui/dialog";
import { consoleStore as store, requestKey, type ConsoleState } from "./store";
import { native, openNativeThread, type Obj } from "./native";
import { allPages, Feedback, useAction } from "./capability-controls";
import { FileSearchSession, fileMatchPath, type FileMatch } from "./file-search";
import { observeNative } from "./native-events";
import { openFile } from "./workspace-file";
import { ProjectDialog } from "./project-controls";

export function ChatSearch({ open, onOpenChange, state }: { open: boolean; onOpenChange: (open: boolean) => void; state: ConsoleState }) {
  const [mode, setMode] = useState<"chats" | "files">("chats");
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  const [rows, setRows] = useState<Obj[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [files, setFiles] = useState<FileMatch[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Obj>();
  const [hits, setHits] = useState<Obj[]>([]);
  const [folder, setFolder] = useState(false);
  const [folderPath, setFolderPath] = useState<string>();
  const search = useRef<FileSearchSession | undefined>(undefined);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const generation = useRef(0);
  const queryRef = useRef(query); queryRef.current = query;
  const action = useAction();
  const cwd = store.cwd;
  useEffect(() => { if (open) { setQuery(""); setMode("chats"); setSelected(undefined); setHits([]); setError(""); } }, [open]);
  useEffect(() => {
    if (!open || mode !== "files") return;
    setFiles([]); setError("");
    const session = new FileSearchSession({ call: native, observe: observeNative }, requestKey(), [cwd],
      (matches, receivedQuery) => { if (receivedQuery === queryRef.current) { setFiles(matches); setLoading(false); } }, (error) => { setError(String(error)); setLoading(false); });
    search.current = session;
    return () => { search.current = undefined; void session.close().catch(store.fail); };
  }, [open, mode, cwd]);
  useEffect(() => {
    const version = ++generation.current;
    if (!open) return;
    setLoading(true); setError(""); setRows([]); setFiles([]); setCursor(null); setSelected(undefined); setHits([]);
    const timer = setTimeout(async () => {
      if (mode === "files") { search.current?.update(query); return; }
      try {
        const params = { limit: 30, archived, sortKey: "updated_at" };
        const results = await Promise.all([
          native("thread/list", { ...params, ...(query.trim() ? { searchTerm: query.trim() } : {}) }),
          query.trim() ? native("thread/search", { ...params, searchTerm: query.trim() }) : Promise.resolve<Obj>({ data: [] }),
        ]);
        if (version !== generation.current) return;
        const merged = new Map<string, Obj>();
        for (const thread of results[0].data) merged.set(thread.id, { thread });
        for (const row of results[1].data) merged.set(row.thread.id, row);
        setRows([...merged.values()]); setCursor(results[1].nextCursor || results[0].nextCursor || null);
      } catch (error) { if (version === generation.current) setError(String(error)); }
      finally { if (version === generation.current) setLoading(false); }
    }, query ? 180 : 0);
    return () => { clearTimeout(timer); generation.current++; };
  }, [open, query, archived, mode]);
  const openChat = async (thread: Obj, hit?: Obj) => action.run(async () => {
    const session = store.state.sessions.find((s) => s.thread_id === thread.id && !s.archived);
    if (session) await store.select(session.id); else await openNativeThread(thread.id);
    if (hit) {
      await store.locateItem(hit.itemId);
      window.dispatchEvent(new CustomEvent("codex-locate-message", { detail: { threadId: thread.id, itemId: hit.itemId } }));
    }
    onOpenChange(false);
  }, "");
  const keys = (event: KeyboardEvent) => {
    if (event.nativeEvent.isComposing) return;
    const buttons = [...(list.current?.querySelectorAll<HTMLButtonElement>('[data-search-choice]:not(:disabled)') || [])];
    if (event.ctrlKey && /^[1-9]$/.test(event.key)) { event.preventDefault(); buttons[Number(event.key) - 1]?.click(); return; }
    if (!["ArrowDown", "ArrowUp", "Enter"].includes(event.key) || !buttons.length) return;
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === "Enter") { if (event.target === input.current) { event.preventDefault(); buttons[0]?.click(); } return; }
    event.preventDefault();
    buttons[(index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length]?.focus();
  };
  return <>
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="chat-search-dialog" showCloseButton={false} onKeyDown={keys}>
        <DialogTitle className="sr-only">{mode === "chats" ? "搜索聊天" : "搜索文件"}</DialogTitle>
        <DialogDescription className="sr-only">输入搜索内容，用上下方向键选择，回车打开。</DialogDescription>
        <div className="chat-search-input">{mode === "files" && <button type="button" aria-label="返回聊天搜索" onClick={() => { setMode("chats"); setQuery(""); }}><ArrowLeft size={16} /></button>}
          <input ref={input} autoFocus type="search" aria-label={mode === "chats" ? "搜索聊天" : "搜索文件"} placeholder={mode === "chats" ? "搜索聊天" : "搜索文件"} value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <div className="chat-search-results" ref={list}>
          <p className="chat-search-caption">{mode === "chats" ? "聊天" : "文件"}
            {query && mode === "chats" && <label><input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />已归档</label>}
          </p>
          {mode === "chats" ? rows.map(({ thread, snippet }, index) => <div key={thread.id} className="chat-search-result">
            <button type="button" data-search-choice disabled={action.busy} onClick={() => void openChat(thread)}>
              <MessageSquare size={14} /><span><strong>{thread.name || thread.preview || thread.id}</strong>{query && snippet && <small>{snippet}</small>}</span>
              <em>{store.state.projectLayout.projects.find((p) => p.id === thread.projectId)?.name || thread.cwd?.split("/").at(-1)}</em>{index < 9 && <kbd>⌃{index + 1}</kbd>}
            </button>
            {snippet && query && <button type="button" className="chat-search-occurrences" disabled={action.busy} onClick={() => void action.run(async () => {
              const version = generation.current;
              setSelected(thread); const result = await allPages("thread/searchOccurrences", { threadId: thread.id, searchTerm: query.trim() });
              if (version === generation.current) setHits(result);
            }, "")}>查看命中位置</button>}
          </div>) : files.map((file) => <button type="button" data-search-choice className="chat-search-file" key={fileMatchPath(file)} onClick={() => {
            if (file.match_type === "directory") { setFolderPath(fileMatchPath(file)); setFolder(true); } else openFile(fileMatchPath(file)); onOpenChange(false);
          }}>{file.match_type === "directory" ? <Folder size={15} /> : <FileText size={15} />}<span>{file.file_name}<small>{fileMatchPath(file)}</small></span></button>)}
          {cursor && mode === "chats" && <button type="button" disabled={action.busy} className="chat-search-more" onClick={() => void action.run(async () => {
            const version = generation.current;
            // Reload both paginated sources in full to preserve each method's opaque cursor.
            const [titles, body] = await Promise.all([allPages("thread/list", { archived, sortKey: "updated_at", ...(query.trim() ? { searchTerm: query.trim() } : {}) }), query.trim() ? allPages("thread/search", { archived, searchTerm: query.trim() }) : Promise.resolve([])]);
            const combined = new Map<string, Obj>(titles.map((thread) => [thread.id, { thread }])); for (const row of body) combined.set(row.thread.id, row);
            if (version === generation.current) { setRows([...combined.values()]); setCursor(null); }
          }, "")}>显示更多</button>}
          {selected && <div className="chat-search-hits"><p>{selected.name || "正文命中位置"}</p>{hits.map((hit) => <button type="button" key={hit.itemId + hit.snippetMatchRange.start} disabled={action.busy} onClick={() => void openChat(selected, hit)}>
            {hit.snippet.slice(0, hit.snippetMatchRange.start)}<mark>{hit.snippet.slice(hit.snippetMatchRange.start, hit.snippetMatchRange.end)}</mark>{hit.snippet.slice(hit.snippetMatchRange.end)}
          </button>)}</div>}
          {loading && <p className="chat-search-caption">正在搜索…</p>}
          {!loading && !(mode === "chats" ? rows.length : files.length) && <p className="chat-search-caption">没有找到结果</p>}
          <Feedback error={error || action.error} />
          {mode === "chats" && !query && <><p className="chat-search-caption">快捷操作</p>
            <button type="button" data-search-choice className="chat-search-quick" onClick={() => { void store.select(null); onOpenChange(false); }}><SquarePen size={15} />新聊天</button>
            <button type="button" data-search-choice className="chat-search-quick" onClick={() => { onOpenChange(false); setFolderPath(undefined); setFolder(true); }}><Folder size={15} />打开文件夹</button>
            <button type="button" data-search-choice className="chat-search-quick" onClick={() => { setMode("files"); input.current?.focus(); }}><Search size={15} />搜索文件</button>
          </>}
        </div>
      </DialogContent>
    </Dialog>
    <ProjectDialog open={folder} onOpenChange={setFolder} initialPath={folderPath} />
  </>;
}
