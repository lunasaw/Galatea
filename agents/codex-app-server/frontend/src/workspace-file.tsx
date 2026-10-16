import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "./components/ui/dialog";
import { native, decode64 } from "./native";

export function localFileTarget(
  href: string | undefined,
): { path: string; line?: number } | null {
  if (!href || !href.startsWith("/") || href.startsWith("//")) return null;
  let decoded: string;
  try {
    decoded = decodeURIComponent(href);
  } catch {
    return null;
  }
  if (/[\x00-\x1f]/.test(decoded)) return null;
  const match = decoded.match(/^(.*?)(?::(\d+)|#L(\d+))?$/);
  return match
    ? {
        path: match[1],
        ...(match[2] || match[3] ? { line: Number(match[2] || match[3]) } : {}),
      }
    : null;
}
export function openFile(path: string) {
  const target = localFileTarget(path);
  if (target)
    window.dispatchEvent(new CustomEvent("codex-file", { detail: target }));
}

export function WorkspaceFilePreview() {
  const [target, setTarget] = useState<{ path: string; line?: number } | null>(
    null,
  );
  const [content, setContent] = useState("");
  const [image, setImage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    const listener = (e: Event) => setTarget((e as CustomEvent).detail);
    window.addEventListener("codex-file", listener);
    return () => window.removeEventListener("codex-file", listener);
  }, []);
  useEffect(() => {
    if (!target) return;
    let disposed = false;
    setLoading(true);
    setError("");
    setContent("");
    setImage("");
    void native("fs/readFile", { path: target.path })
      .then((result) => {
        if (disposed) return;
        if (typeof result.dataBase64 !== "string")
          throw new Error("没有收到文件内容");
        const ext = target.path.split(".").at(-1)?.toLowerCase();
        const mime = (
          {
            png: "image/png",
            jpg: "image/jpeg",
            jpeg: "image/jpeg",
            webp: "image/webp",
            gif: "image/gif",
          } as Record<string, string>
        )[ext || ""];
        if (mime) setImage(`data:${mime};base64,${result.dataBase64}`);
        else setContent(decode64(result.dataBase64));
      })
      .catch((e) => {
        if (!disposed) setError(String(e));
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [target]);
  return (
    <Dialog
      open={!!target}
      onOpenChange={(open) => {
        if (!open) setTarget(null);
      }}
    >
      <DialogContent className="workspace-file-dialog">
        <DialogHeader>
          <DialogTitle>{target?.path.split("/").at(-1)}</DialogTitle>
          <DialogDescription>
            {target?.path}
            {target?.line ? ` · 第 ${target.line} 行` : ""}
          </DialogDescription>
        </DialogHeader>
        {loading ? (
          <p role="status">正在读取文件…</p>
        ) : error ? (
          <p role="alert">{error}</p>
        ) : image ? (
          <img src={image} alt={target?.path.split("/").at(-1)} />
        ) : (
          <pre className="workspace-file-content">{content}</pre>
        )}
      </DialogContent>
    </Dialog>
  );
}
