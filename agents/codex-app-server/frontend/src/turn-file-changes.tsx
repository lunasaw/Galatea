import { useMemo, useState, useSyncExternalStore } from "react";
import { useAuiState } from "@assistant-ui/react";
import { FileDiff, Undo2, X, Check, LoaderCircle } from "lucide-react";
import { Popover } from "radix-ui";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "./components/ui/dialog";
import { consoleStore as store, busyStatuses } from "./store";
import { sharedMutation } from "./native-mutation";
import { diffLines, reviewFiles, relativeReviewPath, turnDiff, type ReviewFile } from "./workbench-data";
import { undoPatchParams, undoUnavailableReason } from "./undo-file-changes";
import { Light } from "./syntax-highlighter";
import "./turn-file-changes.css";

const languages: Record<string, string> = {
  ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript", py: "python",
  sh: "bash", json: "json", css: "css", html: "xml", svg: "xml", yaml: "yaml", yml: "yaml", sql: "sql",
};
function Counts({ additions, removals }: { additions: number; removals: number }) {
  return <span className="change-counts" aria-label={`新增 ${additions} 行，删除 ${removals} 行`}>
    <span className="change-add">+{additions}</span><span className="change-remove">-{removals}</span>
  </span>;
}
function Path({ path }: { path: string }) {
  const slash = path.lastIndexOf("/");
  return <span className="change-path" title={path}><span>{path.slice(0, slash + 1)}</span>{path.slice(slash + 1)}</span>;
}

export function ChangeDiff({ file }: { file: ReviewFile }) {
  const lines = useMemo(() => diffLines(file.diff), [file.diff]);
  const language = languages[file.path.split(".").at(-1) || ""] || "text";
  return <div className="change-diff-scroll" tabIndex={0} aria-label={`${file.path} 的差异`}>
    <div className="change-diff-lines">
      {lines.map((line, index) => line.kind === "meta" ? null : line.kind === "hunk" ?
        <div className="change-hunk" key={index} aria-label={line.text}><span>···</span><span>{line.text}</span></div> :
        <div className="change-diff-line" key={index} data-change={line.kind}>
          <span className="change-line-number" aria-label={`${line.kind === "remove" ? "原" : "新"}行号`}>{line.newLine ?? line.oldLine ?? ""}</span>
          <span className="change-line-sign" aria-hidden="true">{line.kind === "add" ? "+" : line.kind === "remove" ? "−" : " "}</span>
          <Light language={language} useInlineStyles={false} PreTag="div" CodeTag="code">{line.text || " "}</Light>
        </div>)}
    </div>
  </div>;
}

function FilePreview({ file, cwd }: { file: ReviewFile; cwd: string }) {
  return <Popover.Root><Popover.Trigger asChild>
    <button className="change-file-row" aria-label={`查看 ${relativeReviewPath(file.path, cwd)} 的变更`}>
      <Path path={relativeReviewPath(file.path, cwd)} /><Counts {...file} />
    </button>
  </Popover.Trigger><Popover.Portal><Popover.Content className="change-preview" side="bottom" align="start" sideOffset={3} collisionPadding={16} aria-label="文件变更预览">
    <div className="change-preview-heading"><Path path={relativeReviewPath(file.path, cwd)} /><Counts {...file} />
      <Popover.Close aria-label="关闭文件变更预览"><X size={16} /></Popover.Close>
    </div>
    <ChangeDiff file={file} />
  </Popover.Content></Popover.Portal></Popover.Root>;
}

export function TurnFileChanges() {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const turnId = useAuiState((s) => s.message.metadata.custom?.fileChangesTurnId) as string | undefined;
  const session = state.sessions.find((s) => s.id === state.active);
  const cwd = session?.cwd || state.nativeProtocol?.cwd || "";
  const files = useMemo(() => reviewFiles(state.snapshot, turnId), [state.snapshot, turnId]);
  const patch = turnDiff(state.snapshot, turnId);
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [undoneIdentity, setUndoneIdentity] = useState("");
  const storageKey = `codex:undone-files:${state.active}:${turnId}`;
  let undone = false;
  try { undone = localStorage.getItem(storageKey) === patch; } catch { /* storage may be disabled */ }
  // The state also retains success when persistent browser storage is unavailable.
  undone ||= undoneIdentity === storageKey;
  if (!turnId || !files.length) return null;
  const additions = files.reduce((sum, f) => sum + f.additions, 0);
  const removals = files.reduce((sum, f) => sum + f.removals, 0);
  const unavailable = session?.active_turn_id || busyStatuses.has(session?.status || "") ? "请等待当前回复结束后撤销"
    : state.nativeProtocol?.platform === "windows" ? "当前平台暂不支持撤销文件变更"
    : !state.nativeProtocol?.methods.includes("command/exec") ? "当前服务不支持撤销文件变更"
    : undoUnavailableReason(patch, cwd);
  const undo = async () => {
    if (busy || unavailable || !patch || undone) return;
    setBusy(true); setError("");
    try {
      const result = await sharedMutation(storageKey).run("command/exec", {
        ...undoPatchParams(cwd, patch),
        ...(session?.settings?.sandboxPolicy ? { sandboxPolicy: session.settings.sandboxPolicy } : {}),
      });
      if (result.exitCode !== 0) throw new Error(result.stderr?.trim() || "撤销失败，请重试");
      if (!JSON.parse(result.stdout || "{}").undone) throw new Error("未收到撤销结果，请检查文件状态");
      try { localStorage.setItem(storageKey, patch); } catch { /* keep local success */ }
      setUndoneIdentity(storageKey); setConfirm(false);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return <section className="turn-file-changes" aria-label="本轮文件变更" data-turn-id={turnId} data-undone={undone || undefined}>
    <div className="change-card-heading">
      <span className="change-card-icon"><FileDiff size={23} strokeWidth={1.7} /></span>
      <div className="change-card-summary"><strong>{undone ? `已撤销 ${files.length} 个文件的变更` : `已编辑 ${files.length} 个文件`}</strong><Counts additions={additions} removals={removals} /></div>
      <div className="change-card-actions"><button className="change-undo" disabled={busy || undone || !!unavailable} title={unavailable || undefined} onClick={() => { setError(""); setConfirm(true); }}>
        {undone ? <>已撤销 <Check size={16} /></> : <>撤销 <Undo2 size={16} /></>}
      </button><button className="change-view" onClick={() => setOpen(true)}>查看变更</button></div>
    </div>
    <div className="change-file-list">{files.map((file) => <FilePreview key={file.path} file={file} cwd={cwd} />)}</div>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent className="changes-dialog">
      <div className="changes-dialog-heading"><DialogTitle>本轮文件变更</DialogTitle><DialogDescription>{files.length} 个文件 · 新增 {additions} 行，删除 {removals} 行{undone ? " · 已撤销" : ""}</DialogDescription></div>
      <div className="changes-dialog-files">{files.map((file) => <section key={file.path}>
        <div className="change-preview-heading"><Path path={relativeReviewPath(file.path, cwd)} /><Counts {...file} /></div><ChangeDiff file={file} />
      </section>)}</div>
    </DialogContent></Dialog>
    <Dialog open={confirm} onOpenChange={(value) => { if (!busy) setConfirm(value); }}><DialogContent className="changes-undo-dialog">
      <DialogTitle>撤销本轮文件变更？</DialogTitle>
      <DialogDescription>撤销这 {files.length} 个文件在本轮中的修改，保留对话记录。如果文件后来又有修改，将停止撤销。</DialogDescription>
      {error && <p className="change-error" role="alert">{error}</p>}
      {unavailable && <p role="status">{unavailable}</p>}
      <div className="changes-undo-actions"><button onClick={() => setConfirm(false)} disabled={busy}>取消</button>
        <button className="change-view" onClick={() => void undo()} disabled={busy || !!unavailable}>{busy ? <><LoaderCircle size={14} className="animate-spin" />正在撤销…</> : "撤销文件变更"}</button></div>
    </DialogContent></Dialog>
  </section>;
}
