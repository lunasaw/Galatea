import { api, requestKey } from "./store";
import { native, encode64, type Obj } from "./native";

export type TerminalKind = "command" | "process";
export type TerminalSession = {
  id: string; kind: TerminalKind; cwd: string; command: string[];
  status: "starting" | "running" | "exited" | "unknown";
  chunks: Uint8Array[]; exitCode?: number; error: string;
  cursor: string; baseline: string; size: { cols: number; rows: number }; capReached: boolean;
  options?: Obj; advancedRequest?: boolean; tty?: boolean;
};
const storageKey = "codex-agent.terminal-handles";
const listeners = new Set<() => void>();
let sessions: TerminalSession[] = [];
let version = 0; let restored = false;
const emit = () => { version++; listeners.forEach((fn) => fn()); };
const persist = () => {
  try { sessionStorage.setItem(storageKey, JSON.stringify(sessions.map(({ chunks, options, ...row }) => row))); }
  catch { /* Output is kept in the native event history; never evict a live handle. */ }
};
const requests = new Map<string, () => void>();
const paramsFor = (row: TerminalSession) => {
  const params: Obj = { tty: true, size: { cols: 100, rows: 24 }, outputBytesCap: 2 * 1024 * 1024,
    ...row.options, command: row.command, cwd: row.cwd };
  if (!params.tty) delete params.size;
  if (params.disableOutputCap) delete params.outputBytesCap;
  if (row.kind === "command" && !Object.hasOwn(params, "timeoutMs") && !Object.hasOwn(params, "disableTimeout")) params.disableTimeout = true;
  if (row.kind === "process" && !Object.hasOwn(params, "timeoutMs")) params.timeoutMs = null;
  return params;
};

function connect(row: TerminalSession, spawn: boolean) {
  let polling: Promise<void> | undefined; let timer: ReturnType<typeof setTimeout> | undefined;
  let done = false; let failures = 0;
  const stop = () => { done = true; clearTimeout(timer); requests.delete(row.id); };
  requests.set(row.id, stop);
  const finish = (result: Obj) => {
    if (row.status === "exited") return;
    if (result.stdout) row.chunks.push(new TextEncoder().encode(result.stdout));
    if (result.stderr) row.chunks.push(new TextEncoder().encode(result.stderr));
    row.capReached ||= !!(result.stdoutCapReached || result.stderrCapReached);
    row.status = "exited"; row.exitCode = result.exitCode; row.error = ""; persist(); emit();
  };
  const drain = () => polling ||= (async () => {
    let more = true;
    while (more) {
      const page = await api<Obj>("/api/events/history?limit=200&after=" + encodeURIComponent(row.cursor));
      if (page.coverage?.status !== "complete") throw new Error("终端输出历史不完整，请核对进程状态");
      for (const event of page.events) {
        const p = event.payload?.params;
        if (!p || (row.kind === "command" ? p.processId : p.processHandle) !== row.id) continue;
        if (event.method === (row.kind === "command" ? "command/exec/outputDelta" : "process/outputDelta")) {
          row.chunks.push(Uint8Array.from(atob(p.deltaBase64), (ch) => ch.charCodeAt(0)));
          row.capReached ||= p.capReached; emit();
        } else if (row.kind === "process" && event.method === "process/exited") { finish(p); stop(); }
      }
      row.cursor = page.next_cursor; more = page.has_more;
    }
  })().finally(() => { polling = undefined; });
  const poll = async () => {
    try {
      await drain(); failures = 0;
      if (!spawn && row.advancedRequest && row.status !== "exited") {
        // Recover only the Host receipt; credentials from env overrides never
        // enter browser storage and a reload cannot submit a new process.
        const receipt = await api<Obj>("/api/native/receipts/" + encodeURIComponent(row.id));
        if (receipt.error || receipt.rejected) {
          row.status = "unknown"; stop(); persist();
          throw new Error(JSON.stringify(receipt.error || receipt.rejected));
        }
        if (receipt.result) {
          if (row.kind === "command") { await drain(); finish(receipt.result); stop(); }
          else if (row.status === "starting") { row.status = "running"; persist(); emit(); }
        }
      }
    }
    catch (error) {
      row.error = String(error); failures++;
      if (/EPOCH_MISMATCH|历史不完整/.test(row.error)) { row.status = "unknown"; stop(); persist(); }
      emit();
    }
    if (!done) timer = setTimeout(poll, Math.min(10000, 250 * 2 ** Math.min(failures, 6)));
  };
  void poll();
  if (!spawn && row.advancedRequest) { persist(); emit(); return; }
  if (row.kind === "command") {
    if (spawn) row.status = "running";
    // Replaying this exact request after a browser reload only retrieves the
    // Host's durable receipt. It cannot launch the command twice.
    void native("command/exec", { ...paramsFor(row), processId: row.id }, row.id).then(async (result) => {
      await drain(); await drain(); finish(result);
    }).catch((error) => { row.status = "unknown"; row.error = String(error); persist(); emit(); }).finally(stop);
  } else {
    // Resolve a possibly lost spawn response with the original receipt as well.
    // This also makes a reload during startup recover its running state.
    void native("process/spawn", { ...paramsFor(row), processHandle: row.id }, row.id).then(() => {
      if (row.status === "starting") row.status = "running"; persist(); emit();
    }).catch((error) => { stop(); row.status = "unknown"; row.error = String(error); persist(); emit(); });
  }
  persist(); emit();
}

export const terminals = {
  subscribe(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn); }; },
  snapshot: () => version,
  list: () => sessions,
  restore() {
    if (restored) return; restored = true;
    try {
      const rows = JSON.parse(sessionStorage.getItem(storageKey) || "[]");
      if (!Array.isArray(rows)) return;
      for (const saved of rows) {
        if (!saved.id || !["command", "process"].includes(saved.kind) || !saved.baseline || !Array.isArray(saved.command)) continue;
        const row: TerminalSession = { ...saved, chunks: [], cursor: saved.baseline, error: "" };
        sessions.push(row);
        // Replay output from its original watermark, including exited processes.
        if (row.status === "exited") row.status = row.kind === "process" ? "running" : "starting";
        connect(row, false);
      }
      emit();
    } catch { /* An invalid cache does not create or launch any process. */ }
  },
  async start(kind: TerminalKind, cwd: string, command: string[], options?: Obj) {
    if (!cwd.trim() || !command[0]?.trim()) throw new Error("请输入工作目录和可执行程序");
    const baseline = await api<Obj>("/api/events/history?limit=1");
    const row: TerminalSession = { id: requestKey(), kind, cwd, command, status: "starting", chunks: [], error: "",
      cursor: baseline.watermark, baseline: baseline.watermark, size: { cols: 100, rows: 24 }, capReached: false,
      options, tty: options?.tty !== false, advancedRequest: !!options && Object.keys(options).length > 0 };
    sessions = [...sessions, row]; connect(row, true); return row.id;
  },
  async input(row: TerminalSession, data: string) {
    await native(row.kind === "command" ? "command/exec/write" : "process/writeStdin", {
      [row.kind === "command" ? "processId" : "processHandle"]: row.id, deltaBase64: encode64(data),
    });
  },
  async resize(row: TerminalSession, cols: number, rows: number) {
    if (row.tty === false) return;
    if (row.size.cols === cols && row.size.rows === rows || row.status !== "running") return;
    await native(row.kind === "command" ? "command/exec/resize" : "process/resizePty", {
      [row.kind === "command" ? "processId" : "processHandle"]: row.id, size: { cols, rows },
    });
    row.size = { cols, rows }; persist();
  },
  async stop(row: TerminalSession) {
    await native(row.kind === "command" ? "command/exec/terminate" : "process/kill", {
      [row.kind === "command" ? "processId" : "processHandle"]: row.id,
    });
  },
  remove(row: TerminalSession) {
    if (row.status !== "exited") throw new Error("请先停止进程，再关闭标签");
    requests.get(row.id)?.(); sessions = sessions.filter((s) => s !== row); persist(); emit();
  },
};
