import { useEffect, useState } from "react";
import { useAui } from "@assistant-ui/react";
import { BookOpen, Puzzle, Plug } from "lucide-react";
import { native, type Obj } from "./native";
import { consoleStore as store } from "./store";
import { skillReferences } from "./skill-mentions";
export type NativeReference = { type: "skill" | "mention"; name: string; path: string };
export function referenceInput(part: { type: string; name?: string; data?: unknown }): NativeReference[] {
  if (part.type !== "data" || part.name !== "codex-native-reference") return [];
  const value = part.data as NativeReference;
  if (!value || !["skill", "mention"].includes(value.type) || typeof value.name !== "string" || typeof value.path !== "string") return [];
  if (value.type === "mention" && !/^(app|plugin):\/\//.test(value.path)) return [];
  return [{ type: value.type, name: value.name, path: value.path }];
}
export function useAddReference() {
  const aui = useAui();
  return async (row: NativeReference & { label?: string }) => {
    const composer = aui.composer();
    if (composer.getState().attachments.some((a) => a.content?.some((part) => referenceInput(part)[0]?.path === row.path))) return;
    await composer.addAttachment({ type: "引用", name: row.label || row.name, contentType: "text/x-codex-native-reference", content: [{ type: "data", name: "codex-native-reference", data: { type: row.type, name: row.name, path: row.path } }] });
  };
}
export function ComposerReferences({ threadId, disabled, selected }: { threadId?: string; disabled: boolean; selected: () => void }) {
  const addReference = useAddReference(); const [rows, setRows] = useState<Array<NativeReference & { group: string; label: string; description?: string }>>([]);
  const [error, setError] = useState(""); const [loading, setLoading] = useState(true); const [query, setQuery] = useState("");
  const cwd = store.cwd;
  useEffect(() => {
    let disposed = false;
    void Promise.allSettled([native("skills/list", { cwds: cwd ? [cwd] : [] }), native("plugin/installed", { cwds: cwd ? [cwd] : [] }), native("app/installed", threadId ? { threadId } : {})]).then((results) => {
      if (disposed) return;
      const entries: typeof rows = []; const errors: string[] = [];
      results.forEach((result, index) => {
        if (result.status === "rejected") { errors.push(`${["Skills", "插件", "Apps"][index]}：${result.reason}`); return; }
        const data = result.value;
        if (index === 0) entries.push(...skillReferences(data).map((row) => ({ ...row, group: "Skills" })));
        if (index === 1) for (const item of (data.marketplaces || []).flatMap((r: Obj) => r.plugins || [])) {
          if (item.installed && item.enabled && item.availability !== "DISABLED_BY_ADMIN") entries.push({ type: "mention", name: item.name, path: `plugin://${item.id}`, group: "插件", label: item.interface?.displayName || item.name, description: item.interface?.shortDescription });
        }
        if (index === 2) for (const item of data.apps || []) {
          if (item.enabled && item.callable) entries.push({ type: "mention", name: item.runtimeName || item.id, path: `app://${item.id}`, group: "Apps", label: item.runtimeName || item.id });
        }
      });
      setRows([...new Map(entries.map((row) => [row.path, row])).values()]); setError(errors.join("\n")); setLoading(false);
    });
    return () => { disposed = true; };
  }, [threadId, cwd]);
  return <div className="composer-reference-options">
    <input aria-label="搜索 Apps、插件和 Skills" type="search" placeholder="搜索 Apps、插件和 Skills" value={query} onChange={(e) => setQuery(e.target.value)} />
    {loading && <small>正在读取可用扩展…</small>}
    {["Apps", "插件", "Skills"].map((group) => <div key={group}><p>{group}</p>{rows.filter((r) => r.group === group && `${r.label} ${r.description || ""}`.toLowerCase().includes(query.toLowerCase())).map((row) => {
      const Icon = group === "Skills" ? BookOpen : group === "插件" ? Puzzle : Plug;
      return <button type="button" role="menuitem" key={row.path} disabled={disabled} onClick={() => {
        void addReference(row).then(selected).catch(store.fail);
      }}><Icon /><span>{row.label}</span><small>{row.description}</small></button>;
    })}</div>)}
    {!loading && !rows.length && <small>暂无可引用的扩展</small>}
    {error && <small role="alert">{error}</small>}
  </div>;
}
