import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useAuiState } from "@assistant-ui/react";
import { GitFork, Undo2 } from "lucide-react";
import {
  consoleStore as store,
  busyStatuses,
  api,
  requestKey,
  ApiError,
} from "./store";
import { Button } from "./components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "./components/ui/dialog";
import { TooltipIconButton } from "./components/assistant-ui/elements/tooltip-icon-button";
import { readTurns, rollbackRequest, type NativeTurn } from "./thread-history";

export type ThreadAction =
  "fork" | "archive" | "unarchive" | "delete" | "undo" | "rollback";
type Target = {
  action: ThreadAction;
  sessionId?: string;
  threadId?: string;
  turnId?: string;
  title?: string;
};
export function requestThreadAction(target: Target) {
  window.dispatchEvent(
    new CustomEvent("codex-thread-action", { detail: target }),
  );
}
const labels: Record<ThreadAction, string> = {
  fork: "分叉会话",
  archive: "归档会话",
  unarchive: "取消归档",
  delete: "删除会话",
  undo: "回退上一轮",
  rollback: "回退到指定轮次",
};

export function MessageTurnActions() {
  const turnId = useAuiState((s) => s.message.metadata.custom?.turnId);
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const session = state.sessions.find((s) => s.id === state.active);
  if (!turnId || !session) return null;
  const disabled =
    state.settingsBusy ||
    !!session.active_turn_id ||
    busyStatuses.has(session.status);
  return (
    <>
      <TooltipIconButton
        tooltip="从此轮分叉"
        disabled={disabled}
        onClick={() =>
          requestThreadAction({
            action: "fork",
            sessionId: session.id,
            turnId: String(turnId),
          })
        }
      >
        <GitFork />
      </TooltipIconButton>
      <TooltipIconButton
        tooltip="回退到此轮之前"
        disabled={disabled}
        onClick={() =>
          requestThreadAction({
            action: "rollback",
            sessionId: session.id,
            turnId: String(turnId),
          })
        }
      >
        <Undo2 />
      </TooltipIconButton>
    </>
  );
}

export function ThreadActionDialog() {
  const [target, setTarget] = useState<Target | null>(null);
  const [turns, setTurns] = useState<NativeTurn[]>([]);
  const [selected, setSelected] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const prepared = useRef<{
    method: string;
    params: Record<string, unknown>;
    key: string;
  } | null>(null);
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const session = state.sessions.find(
    (s) =>
      (target?.sessionId && s.id === target.sessionId) ||
      (target?.threadId && s.thread_id === target.threadId),
  );
  const threadId = target?.threadId || session?.thread_id;
  const rollback = target?.action === "undo" || target?.action === "rollback";
  useEffect(() => {
    const listener = (event: Event) => {
      if (store.state.settingsBusy) return;
      const value = (event as CustomEvent<Target>).detail;
      prepared.current = null;
      setError("");
      setTurns([]);
      setLoading(false);
      setSelected(value.turnId || "");
      setTarget(value);
    };
    window.addEventListener("codex-thread-action", listener);
    return () => window.removeEventListener("codex-thread-action", listener);
  }, []);
  useEffect(() => {
    if (!target || !threadId || !rollback) return;
    let cancelled = false;
    setLoading(true);
    readTurns(store, threadId)
      .then(({ turns }) => {
        if (cancelled) return;
        setTurns(turns);
        setSelected(target.turnId || turns.at(-1)?.id || "");
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [target, threadId, rollback]);
  const apply = async () => {
    if (!target || !threadId || busy || state.settingsBusy) return;
    if (
      session?.active_turn_id ||
      (session && busyStatuses.has(session.status))
    ) {
      setError("请先停止当前回复，再操作会话。");
      return;
    }
    setBusy(true);
    setError("");
    store.patch({ settingsBusy: true });
    const originalActive = store.state.active;
    const projectId = session
      ? store.projectForSession(session.id)?.id || ""
      : undefined;
    try {
      if (!prepared.current) {
        const request = rollback
          ? rollbackRequest(
              threadId,
              await readTurns(store, threadId),
              selected,
            )
          : {
              method: `thread/${target.action}`,
              params: {
                threadId,
                ...(target.action === "fork"
                  ? {
                      deferGoalContinuation: true,
                      ...(target.turnId ? { lastTurnId: target.turnId } : {}),
                    }
                  : {}),
              },
            };
        prepared.current = { ...request, key: requestKey() };
      }
      const { method, params, key } = prepared.current;
      const result = await api<any>(
        "/api/native/call",
        { method, params },
        key,
      );
      if (target.action === "fork" && result.session && projectId !== undefined)
        store.saveProjectLayout({
          threadProjects: { [result.session.id]: projectId },
        });
      await store.refreshSessions();
      if (store.state.active === originalActive) {
        if (target.action === "fork" && result.session)
          await store.select(result.session.id);
        else if (
          (target.action === "archive" || target.action === "delete") &&
          session?.id === originalActive
        )
          await store.select(null);
        else if (rollback && session?.id === originalActive)
          await store.select(originalActive);
      }
      window.dispatchEvent(new Event("codex-threads-changed"));
      setTarget(null);
    } catch (e) {
      if (e instanceof ApiError && [409, 422].includes(e.status))
        prepared.current = null;
      setError(
        String(e).includes("forked history still references")
          ? "此会话的历史仍被分叉会话引用，Codex 暂不允许删除。可以先归档此会话，或处理相关分叉后再删除。"
          : String(e),
      );
    } finally {
      setBusy(false);
      store.patch({ settingsBusy: false });
    }
  };
  const index = turns.findIndex((t) => t.id === selected);
  return (
    <Dialog
      open={!!target}
      onOpenChange={(open) => {
        if (!open && !busy) setTarget(null);
      }}
    >
      <DialogContent
        onEscapeKeyDown={(event) => {
          if (busy) event.preventDefault();
        }}
        onInteractOutside={(event) => {
          if (busy) event.preventDefault();
        }}
      >
        <DialogTitle>{target ? labels[target.action] : "会话操作"}</DialogTitle>
        <DialogDescription>
          {target?.action === "delete"
            ? "永久删除此会话及其派生的子 Agent 会话，无法恢复。工作目录中的文件会保留。"
            : target?.action === "archive"
              ? "会话将从侧栏隐藏，可以在“恢复会话 → 已归档”中取消归档。"
              : rollback
                ? "移除所选轮次及之后的对话历史，回到该轮开始之前。此操作不会撤销工作目录中的文件修改。"
                : target?.action === "fork"
                  ? target.turnId
                    ? "复制截至此轮（包含此轮）的对话，创建独立会话。原会话保持不变；工作目录中的文件仍然共享。"
                    : "复制当前对话，创建独立会话。原会话保持不变；工作目录中的文件仍然共享。"
                  : "将会话恢复到会话列表。"}
        </DialogDescription>
        <p className="truncate text-sm">
          {target?.title || (session ? store.title(session.id) : threadId)}
        </p>
        {rollback && (
          <label className="grid gap-2 text-sm">
            从哪一轮开始移除
            <select
              aria-label="回退轮次"
              className="rounded-md border bg-background p-2"
              value={selected}
              disabled={busy || loading || !!prepared.current}
              onChange={(event) => setSelected(event.target.value)}
            >
              {turns.map((turn, i) => (
                <option key={turn.id} value={turn.id}>
                  第 {i + 1} 轮 · {turn.text || turn.id}
                </option>
              ))}
            </select>
            <span className="text-muted-foreground">
              {loading
                ? "正在读取完整会话…"
                : index >= 0
                  ? `将移除 ${turns.length - index} 轮，保留前 ${index} 轮。`
                  : "没有可回退的轮次。"}
            </span>
          </label>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => setTarget(null)}
          >
            取消
          </Button>
          <Button
            variant={
              rollback || target?.action === "delete"
                ? "destructive"
                : "default"
            }
            disabled={busy || loading || !threadId || (rollback && index < 0)}
            onClick={() => void apply()}
          >
            {busy
              ? "处理中…"
              : prepared.current
                ? "重试操作"
                : "确认" + (target ? labels[target.action] : "")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
