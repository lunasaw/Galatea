import { useEffect, useRef, useState } from "react";
import { Button } from "./components/ui/button";
import { Field, Feedback, useAction } from "./capability-controls";
import { encode64, native, type Obj } from "./native";
import { observeNative } from "./native-events";
import { consoleStore as store, requestKey } from "./store";

export function useFileWatch(path: string, enabled: boolean, changed: (paths: string[]) => void) {
  const callback = useRef(changed); callback.current = changed;
  const [error, setError] = useState(""); const [watching, setWatching] = useState(false);
  useEffect(() => {
    if (!path || !enabled) return;
    let disposed = false; let stop: (() => void) | undefined; let registered = false;
    const watchId = requestKey(); setError("");
    const start = async () => {
      stop = await observeNative("fs/changed", (params) => {
        if (!disposed && params.watchId === watchId) callback.current(params.changedPaths || []);
      }, (e) => setError(String(e)));
      if (disposed) { stop(); return; }
      await native("fs/watch", { path, watchId }); registered = true;
      if (disposed) { await native("fs/unwatch", { watchId }); return; }
      setWatching(true);
    };
    void start().catch((e) => { if (!disposed) setError(String(e)); stop?.(); });
    return () => {
      disposed = true; stop?.(); setWatching(false);
      if (registered) void native("fs/unwatch", { watchId }).catch(store.fail);
    };
  }, [path, enabled]);
  return { error, watching };
}

const fileDrafts = new Map<string, { content: string; original: string }>();
// Drafts outlive the panel, so the navigation guard must also outlive it.
window.addEventListener("beforeunload", (event) => {
  if (fileDrafts.size) { event.preventDefault(); event.returnValue = ""; }
});
export function FileCapabilities({ initialPath = store.cwd }: { initialPath?: string }) {
  const [path, setPath] = useState(initialPath); const [loadedPath, setLoadedPath] = useState("");
  const [binary, setBinary] = useState(false); const generation = useRef(0);
  const [content, setContent] = useState(""); const [original, setOriginal] = useState("");
  const [entries, setEntries] = useState<Obj[]>(); const [metadata, setMetadata] = useState<Obj>();
  const [destination, setDestination] = useState(""); const [newDirectory, setNewDirectory] = useState("");
  const [watch, setWatch] = useState(false); const [changes, setChanges] = useState<string[]>([]);
  const [remove, setRemove] = useState(false); const [recursive, setRecursive] = useState(false); const action = useAction();
  const open = async (target: string) => {
    if (content !== original) throw new Error("请先保存文件或放弃编辑，再打开其他路径");
    const current = ++generation.current;
    const meta = await native("fs/getMetadata", { path: target });
    if (current !== generation.current) return;
    if (meta.isDirectory) {
      const result = await native("fs/readDirectory", { path: target }); if (current !== generation.current) return;
      setEntries(result.entries); setContent(""); setOriginal(""); setBinary(false);
    } else if (meta.isFile) {
      const result = await native("fs/readFile", { path: target }); if (current !== generation.current) return;
      const bytes = Uint8Array.from(atob(result.dataBase64), (c) => c.charCodeAt(0));
      let text = ""; let isBinary = false;
      try { text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes); isBinary = text.includes("\0"); }
      catch { isBinary = true; }
      const draft = fileDrafts.get(target);
      setContent(!isBinary ? draft?.content ?? text : ""); setOriginal(!isBinary ? draft?.original ?? text : "");
      setBinary(isBinary); setEntries(undefined);
    }
    setPath(target); setLoadedPath(target); setMetadata(meta); setRemove(false); setChanges([]);
  };
  useEffect(() => { void action.run(() => open(initialPath), ""); return () => { generation.current++; }; }, []);
  useEffect(() => {
    if (!loadedPath) return;
    if (content !== original) fileDrafts.set(loadedPath, { content, original }); else fileDrafts.delete(loadedPath);
  }, [loadedPath, content, original]);
  const watcher = useFileWatch(loadedPath, watch, (paths) => {
    setChanges(paths);
    const current = generation.current;
    if (metadata?.isDirectory) void native("fs/readDirectory", { path: loadedPath }).then((r) => { if (current === generation.current) setEntries(r.entries); }).catch(store.fail);
  });
  return <section className="capability-page"><h3>文件</h3>
    <form onSubmit={(e) => { e.preventDefault(); void action.run(() => open(path), "已读取"); }}>
      <Field label="文件或目录的绝对路径"><input required disabled={action.busy} value={path} onChange={(e) => { generation.current++; setPath(e.target.value); }} /></Field>
      <Button disabled={action.busy || !path.trim() || content !== original}>打开路径</Button>
      {content !== original && <p role="status">有未保存的编辑，请先保存或放弃编辑。</p>}
    </form>
    {metadata && <>
      <dl className="capability-facts"><div><dt>类型</dt><dd>{metadata.isDirectory ? "目录" : metadata.isFile ? "文件" : "其他"}{metadata.isSymlink ? " · 符号链接" : ""}</dd></div>
        <div><dt>修改时间</dt><dd>{metadata.modifiedAtMs == null ? "未提供" : new Date(metadata.modifiedAtMs).toLocaleString()}</dd></div>
        <div><dt>创建时间</dt><dd>{metadata.createdAtMs == null ? "未提供" : new Date(metadata.createdAtMs).toLocaleString()}</dd></div></dl>
      <label className="native-checkbox"><input type="checkbox" checked={watch} onChange={(e) => setWatch(e.target.checked)} />监听文件变化{watcher.watching && " · 已连接"}</label>
      {!!changes.length && <div role="status"><p>文件已在外部变化{content !== original ? "，编辑内容已保留" : ""}。</p><Button variant="outline" disabled={action.busy || content !== original} onClick={() => void action.run(() => open(loadedPath), "已重新读取")}>重新读取</Button></div>}
      {entries ? <ul className="capability-list">{entries.map((entry) => <li key={entry.fileName}><button disabled={action.busy || content !== original}
        onClick={() => void action.run(() => open(loadedPath.replace(/\/$/, "") + "/" + entry.fileName), "已读取")}>{entry.isDirectory ? "▸ " : ""}{entry.fileName}</button></li>)}</ul>
        : binary ? <p>此文件不是 UTF-8 文本，可复制或删除，不能在文本编辑器中修改。</p>
        : metadata.isFile && <><Field label="文件内容"><textarea className="native-editor" value={content} disabled={action.busy} onChange={(e) => setContent(e.target.value)} rows={16} spellCheck={false} /></Field>
          <div className="native-options"><Button disabled={action.busy || content === original} onClick={() => void action.run(async () => {
            const current = await native("fs/readFile", { path: loadedPath });
            if (current.dataBase64 !== encode64(original)) throw new Error("文件已在外部修改。草稿已保留，请核对后重新读取，避免覆盖其他修改");
            await native("fs/writeFile", { path: loadedPath, dataBase64: encode64(content) }); setOriginal(content);
          }, "文件已保存")}>保存文件</Button><Button variant="ghost" disabled={content === original} onClick={() => setContent(original)}>放弃编辑</Button></div></>}
      <form onSubmit={(e) => { e.preventDefault(); void action.run(async () => {
        await native("fs/copy", { sourcePath: loadedPath, destinationPath: destination, recursive: !!metadata.isDirectory });
      }, "复制完成"); }}><Field label="复制到绝对路径"><input required value={destination} onChange={(e) => setDestination(e.target.value)} /></Field><Button variant="outline" disabled={action.busy || !destination.trim()}>复制{metadata.isDirectory ? "目录及内容" : "文件"}</Button></form>
      <Button variant="ghost" disabled={action.busy || content !== original} onClick={() => { setRemove(true); setRecursive(false); }}>删除此{metadata.isDirectory ? "目录" : "文件"}…</Button>
      {remove && <div className="file-delete-confirm"><p>永久删除 {loadedPath}</p>
        {metadata.isDirectory && <label className="native-checkbox"><input type="checkbox" checked={recursive} onChange={(e) => setRecursive(e.target.checked)} />同时删除目录中的所有内容</label>}
        <Button variant="destructive" disabled={action.busy} onClick={() => void action.run(async () => {
          await native("fs/remove", { path: loadedPath, recursive, force: false }); setWatch(false); setMetadata(undefined); setLoadedPath(""); setRemove(false);
        }, "已删除")}>确认删除</Button><Button variant="ghost" onClick={() => setRemove(false)}>取消</Button></div>}
    </>}
    <form onSubmit={(e) => { e.preventDefault(); void action.run(async () => { await native("fs/createDirectory", { path: newDirectory, recursive: true }); await open(newDirectory); }, "目录已创建"); }}>
      <Field label="新目录的绝对路径"><input value={newDirectory} onChange={(e) => setNewDirectory(e.target.value)} required /></Field><Button variant="outline" disabled={action.busy || !newDirectory.trim() || content !== original}>创建目录</Button>
    </form>
    <Feedback error={action.error || watcher.error} status={action.status} />
  </section>;
}
