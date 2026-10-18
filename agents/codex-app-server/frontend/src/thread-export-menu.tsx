import { useEffect, useRef, useState } from "react";
import { Check, Copy, Download, LoaderCircle, X } from "lucide-react";
import { api, consoleStore as store } from "./store";
import { copyText } from "./clipboard";
import { useIsMobile } from "./hooks/use-mobile";
import { exportFilename, exportMarkdown, readExportSnapshot, threadLink } from "./thread-export";
import type { Session, Snapshot } from "./types";
import {
  DropdownMenuItem, DropdownMenuPortal, DropdownMenuSeparator,
  DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger,
} from "./components/ui/dropdown-menu";

type Action = "link" | "markdown" | "cwd" | "download";
type Feedback = { busy: boolean; error?: boolean; text: string };

export function useThreadExport(session: Session | undefined, historyReady: boolean) {
  const isMobile = useIsMobile();
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => {
    if (!feedback || feedback.busy || feedback.error) return;
    const timer = window.setTimeout(() => setFeedback(null), 4000);
    return () => window.clearTimeout(timer);
  }, [feedback]);

  const run = async (action: Action) => {
    if (!session || pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    const title = store.title(session.id);
    setFeedback({ busy: true, text: action === "download" || action === "markdown" ? "正在读取完整会话…" : "正在复制…" });
    try {
      if (action === "link") await copyText(threadLink(window.location.href, session.id));
      else if (action === "cwd") await copyText(session.cwd || session.settings?.cwd || "");
      else {
        const snapshot = await readExportSnapshot((path, signal) => api<Snapshot>(path, undefined, undefined, signal), session.id, controller.signal);
        const markdown = exportMarkdown(snapshot, session, title);
        if (action === "markdown") await copyText(markdown);
        else {
          const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
          const anchor = document.createElement("a");
          anchor.href = url;
          anchor.download = exportFilename(title);
          document.body.append(anchor);
          anchor.click();
          anchor.remove();
          window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
        }
        setFeedback({ busy: false, text: `${action === "download" ? "已导出" : "已复制"}「${title}」为 Markdown${snapshot.coverage.status !== "complete" ? "（部分历史不可用）" : ""}` });
        return;
      }
      setFeedback({ busy: false, text: action === "link" ? "已复制深度链接" : "已复制工作目录" });
    } catch (error) {
      if (!controller.signal.aborted) setFeedback({ busy: false, error: true, text: `${action === "download" ? "导出" : "复制"}失败：${error instanceof Error ? error.message : String(error)}` });
    } finally {
      pending.current = null;
    }
  };

  return {
    menu: session && <>
      <DropdownMenuSub>
        <DropdownMenuSubTrigger disabled={!!feedback?.busy} className="rounded-lg data-[disabled]:opacity-50"><Copy />复制</DropdownMenuSubTrigger>
        <DropdownMenuPortal>
          {/* On narrow screens, overlap the parent below its Copy row instead of placing two menus side by side. */}
          <DropdownMenuSubContent className="thread-copy-menu" collisionPadding={8}
            sideOffset={isMobile ? -220 : 6} alignOffset={isMobile ? 36 : 0}>
            <DropdownMenuItem onSelect={() => void run("link")}><Copy />复制深度链接</DropdownMenuItem>
            <DropdownMenuItem disabled={!historyReady} onSelect={() => void run("markdown")}><Copy />复制为 Markdown</DropdownMenuItem>
            <DropdownMenuItem disabled={!session.cwd && !session.settings?.cwd} onSelect={() => void run("cwd")}><Copy />复制工作目录</DropdownMenuItem>
          </DropdownMenuSubContent>
        </DropdownMenuPortal>
      </DropdownMenuSub>
      <DropdownMenuItem disabled={!historyReady || !!feedback?.busy} onSelect={() => void run("download")}><Download />导出 Markdown</DropdownMenuItem>
      <DropdownMenuSeparator />
    </>,
    status: feedback && <div className="thread-export-status" role={feedback.error ? "alert" : "status"}>
      {feedback.busy ? <LoaderCircle className="animate-spin" /> : feedback.error ? null : <Check />}
      <span>{feedback.text}</span>
      {!feedback.busy && <button type="button" aria-label="关闭导出提示" onClick={() => setFeedback(null)}><X /></button>}
    </div>,
  };
}
