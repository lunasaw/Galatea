import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Terminal as Xterm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { Plus, X } from "lucide-react";
import { Button } from "./components/ui/button";
import { Feedback, Field, useAction } from "./capability-controls";
import { terminals, type TerminalSession, type TerminalKind } from "./terminal-sessions";
import { consoleStore as store } from "./store";
import { SchemaInput } from "./schema-form";
import { resolveSchema } from "./schema-data";
import type { Obj } from "./native";
import "@xterm/xterm/css/xterm.css";

function TerminalView({ row, active }: { row: TerminalSession; active: boolean }) {
  const element = useRef<HTMLDivElement>(null); const term = useRef<Xterm | undefined>(undefined);
  const fit = useRef<FitAddon | undefined>(undefined); const written = useRef(0); const [error, setError] = useState("");
  const version = useSyncExternalStore(terminals.subscribe, terminals.snapshot);
  useEffect(() => {
    const terminal = new Xterm({ cursorBlink: true, fontSize: 13, fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", scrollback: 10000 });
    const addon = new FitAddon(); terminal.loadAddon(addon); terminal.open(element.current!);
    term.current = terminal; fit.current = addon; written.current = 0;
    // Keep input in order even if the preceding HTTP write has not completed.
    let writes = Promise.resolve();
    const input = terminal.onData((data) => {
      if (row.status !== "running") return;
      writes = writes.then(() => terminals.input(row, data)).catch((e) => setError(String(e)));
    });
    const theme = () => {
      const dark = document.documentElement.classList.contains("dark");
      terminal.options.theme = dark ? { background: "#191919", foreground: "#eeeeee", cursor: "#eeeeee" }
        : { background: "#ffffff", foreground: "#222222", cursor: "#222222", selectionBackground: "#cce2ff" };
    };
    const observer = new MutationObserver(theme); observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] }); theme();
    return () => { input.dispose(); observer.disconnect(); terminal.dispose(); term.current = undefined; };
  }, [row.id]);
  useEffect(() => {
    if (!term.current) return;
    for (; written.current < row.chunks.length; written.current++) term.current.write(row.chunks[written.current]);
  }, [version, row]);
  useEffect(() => {
    const fitNow = () => {
      if (!active || !element.current?.offsetWidth || !element.current?.offsetHeight) return;
      fit.current?.fit();
      if (term.current) void terminals.resize(row, term.current.cols, term.current.rows).catch((e) => setError(String(e)));
    };
    const observer = new ResizeObserver(fitNow); observer.observe(element.current!); fitNow();
    if (active) term.current?.focus();
    return () => observer.disconnect();
  }, [active, row.id, row.status]);
  return <div className="pty-pane" hidden={!active}>
    <div className="pty-screen" ref={element} aria-label="交互终端" />
    {row.status === "exited" && <p role="status">进程已退出 · 退出码 {row.exitCode}</p>}
    {row.capReached && <p role="status">输出已达到捕获上限，后续输出可能被截断。</p>}
    <Feedback error={error || row.error} />
  </div>;
}

export function TerminalWorkspace({ compact = false }: { compact?: boolean }) {
  useSyncExternalStore(terminals.subscribe, terminals.snapshot);
  useEffect(() => { terminals.restore(); }, []);
  const [kind, setKind] = useState<TerminalKind>("command"); const [selected, setSelected] = useState("");
  const [creating, setCreating] = useState(false); const action = useAction();
  const [cwd, setCwd] = useState(store.cwd); const [program, setProgram] = useState("/bin/sh"); const [args, setArgs] = useState("-i");
  const [options, setOptions] = useState<Record<TerminalKind, Obj>>({ command: {}, process: {} });
  const root = { definitions: store.state.nativeProtocol?.definitions || {} };
  const schema = resolveSchema(store.state.nativeProtocol?.params[kind === "command" ? "command/exec" : "process/spawn"] || {}, root);
  const managed = ["command", "cwd", "processId", "processHandle"];
  const advancedSchema = { ...schema, required: [], additionalProperties: false,
    properties: Object.fromEntries(Object.entries(schema.properties || {}).filter(([key]) => !managed.includes(key))) };
  const rows = terminals.list().filter((r) => r.kind === kind);
  const current = rows.find((r) => r.id === selected) || rows.at(-1);
  return <section className={`terminal-workspace ${compact ? "is-compact" : ""}`} aria-label="终端工作区">
    {!compact && <h3>终端</h3>}
    <div className="terminal-kind-tabs" role="tablist" aria-label="终端类型">
      {(["command", "process"] as const).map((id) => <button role="tab" aria-selected={kind === id} key={id}
        onClick={() => { setKind(id); setSelected(""); setCreating(!terminals.list().some((r) => r.kind === id)); }}>{id === "command" ? "沙箱命令" : "主机进程"}</button>)}
    </div>
    <div className="terminal-session-tabs" role="tablist" aria-label="终端会话">
      {rows.map((row) => <div key={row.id}><button role="tab" aria-selected={!creating && current === row} title={row.cwd}
        onClick={() => { setSelected(row.id); setCreating(false); }}>{row.command[0].split("/").at(-1)} · {row.cwd.split("/").at(-1) || row.cwd}</button>
        {row.status === "exited" && <button aria-label="关闭已退出的终端" onClick={() => terminals.remove(row)}><X size={12} /></button>}</div>)}
      <button aria-label="新建终端" onClick={() => { setCwd(store.cwd); setCreating(true); }}><Plus size={15} /></button>
      {current && !creating && current.status !== "exited" && <Button size="sm" variant="ghost" disabled={action.busy} onClick={() => void action.run(() => terminals.stop(current), "已请求停止")}>停止进程</Button>}
    </div>
    {creating || !current ? <form className="terminal-start" onSubmit={(e) => { e.preventDefault(); void action.run(async () => {
      const id = await terminals.start(kind, cwd, [program, ...args.split("\n").filter(Boolean)], options[kind]); setSelected(id); setCreating(false);
    }, ""); }}>
      <p className="muted">{kind === "command" ? "按服务端配置的权限运行命令。" : "直接在 app-server 主机运行，不使用 Codex 沙箱。"}</p>
      <Field label="命令工作目录"><input required value={cwd} onChange={(e) => setCwd(e.target.value)} /></Field>
      <Field label="可执行程序"><input required value={program} onChange={(e) => setProgram(e.target.value)} /></Field>
      <Field label="参数（每行一个）"><textarea value={args} onChange={(e) => setArgs(e.target.value)} rows={2} /></Field>
      <details><summary>高级执行设置</summary><SchemaInput key={kind} schema={advancedSchema} root={root} label="执行参数" value={options[kind]} change={(value) => setOptions((all) => ({ ...all, [kind]: value }))} /></details>
      <Button disabled={action.busy || !program.trim() || !cwd.trim()}>启动终端</Button>
    </form> : null}
    {rows.map((row) => <TerminalView key={row.id} row={row} active={!creating && current === row} />)}
    <Feedback error={action.error} status={action.status} />
  </section>;
}
