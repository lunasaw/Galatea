import { cloneElement, isValidElement, useCallback, useEffect, useId, useRef, useState, type ReactElement, type ReactNode } from "react";
import { native, type Obj } from "./native";

export function Field({ label, children }: { label: string; children: ReactNode }) {
  const id = useId();
  const control = isValidElement(children) ? children as ReactElement<{ id?: string }> : null;
  const target = control?.props.id || id;
  return <div className="capability-field"><label htmlFor={target}>{label}</label>{control ? cloneElement(control, { id: target }) : children}</div>;
}
export function Feedback({ error, status }: { error?: string; status?: string }) {
  return <>{error && <p role="alert" className="text-destructive">{error}</p>}{status && <p role="status" className="text-muted-foreground">{status}</p>}</>;
}
export function Facts({ value }: { value: unknown }) {
  if (value == null) return <span className="muted">未提供</span>;
  if (typeof value === "boolean") return <span>{value ? "是" : "否"}</span>;
  if (typeof value !== "object") return <span className="whitespace-pre-wrap">{String(value)}</span>;
  if (Array.isArray(value)) return <ul className="capability-list">{value.map((row, index) => <li key={index}><Facts value={row} /></li>)}</ul>;
  return <dl className="capability-facts">{Object.entries(value).map(([key, item]) => <div key={key}><dt>{key}</dt><dd><Facts value={item} /></dd></div>)}</dl>;
}
export function useAction() {
  const lock = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const run = async (action: () => Promise<void>, message = "已更新") => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(""); setStatus("");
    try { await action(); setStatus(message); }
    catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { lock.current = false; setBusy(false); }
  };
  return { busy, error, status, run };
}
export async function allPages<T = Obj>(method: string, params: Obj = {}): Promise<T[]> {
  const rows: T[] = []; const cursors = new Set<string>(); let cursor: string | null = null;
  do {
    const result: { data: T[]; nextCursor?: string | null } = await native(method, { ...params, limit: 100, ...(cursor ? { cursor } : {}) });
    if (!Array.isArray(result.data)) throw new Error("服务未返回有效列表");
    rows.push(...result.data); cursor = result.nextCursor || null;
    if (cursor && cursors.has(cursor)) throw new Error("服务返回了重复的分页游标");
    if (cursor) cursors.add(cursor);
  } while (cursor);
  return rows;
}
export function useResource<T>(load: () => Promise<T>, poll = 0) {
  const [data, setData] = useState<T>(); const [error, setError] = useState("");
  const [loading, setLoading] = useState(true); const generation = useRef(0);
  const inFlight = useRef(false);
  const refresh = useCallback(async () => {
    const id = ++generation.current; setLoading(true);
    inFlight.current = true;
    try { const value = await load(); if (id === generation.current) { setData(value); setError(""); } }
    catch (error) { if (id === generation.current) setError(String(error)); }
    finally { if (id === generation.current) { setLoading(false); inFlight.current = false; } }
  }, [load]);
  useEffect(() => {
    setData(undefined); void refresh();
    const timer = poll ? setInterval(() => { if (!document.hidden && !inFlight.current) void refresh(); }, poll) : undefined;
    return () => { generation.current++; clearInterval(timer); };
  }, [refresh, poll]);
  return { data, error, loading, refresh };
}
