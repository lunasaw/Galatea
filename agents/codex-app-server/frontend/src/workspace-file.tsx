import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Light } from "react-syntax-highlighter";
import {
  Check,
  Copy,
  FileText,
  LoaderCircle,
  RefreshCw,
  WrapText,
} from "lucide-react";
import { TooltipIconButton } from "./components/assistant-ui/elements/tooltip-icon-button";
import { useCopyToClipboard } from "./hooks/use-copy-to-clipboard";
import { native, decode64 } from "./native";
import { localFileTarget, type FileTarget } from "./file-target";
export { localFileTarget, type FileTarget } from "./file-target";

const WorkspaceDirectory = createContext("");
export function WorkspaceFileScope({
  cwd,
  children,
}: {
  cwd: string;
  children: ReactNode;
}) {
  const parent = useContext(WorkspaceDirectory);
  return (
    <WorkspaceDirectory.Provider value={cwd || parent}>
      {children}
    </WorkspaceDirectory.Provider>
  );
}
function dispatchFile(target: FileTarget) {
  window.dispatchEvent(new CustomEvent("codex-file", { detail: target }));
}
export function openFile(path: string, cwd = "") {
  const target = localFileTarget(path, cwd);
  if (target) dispatchFile(target);
}
export function WorkspaceLink({
  href,
  children,
  className,
}: {
  href?: string;
  children: ReactNode;
  className?: string;
}) {
  const cwd = useContext(WorkspaceDirectory);
  const target = localFileTarget(href, cwd);
  return target ? (
    <button
      type="button"
      className={`workspace-file-link ${className || ""}`}
      title={`${target.path}${target.line ? `:${target.line}` : ""}`}
      onClick={(event) => {
        event.stopPropagation();
        event.preventDefault();
        dispatchFile(target);
      }}
    >
      {children}
    </button>
  ) : (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className={className}
    >
      {children}
    </a>
  );
}
export function WorkspaceImageLink({
  src,
  alt,
}: {
  src?: string;
  alt?: string;
}) {
  const cwd = useContext(WorkspaceDirectory);
  return localFileTarget(src, cwd) ? (
    <WorkspaceLink href={src}>查看图片：{alt || "图片"}</WorkspaceLink>
  ) : (
    <span>[图片：{alt || "图片"}]</span>
  );
}

const languages: Record<string, string> = {
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  py: "python",
  sh: "bash",
  bash: "bash",
  json: "json",
  yml: "yaml",
  yaml: "yaml",
  html: "xml",
  xml: "xml",
  css: "css",
  sql: "sql",
};
const imageTypes: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  webp: "image/webp",
  gif: "image/gif",
};

export function WorkspaceFileView({
  target,
  active,
}: {
  target: FileTarget;
  active: boolean;
}) {
  const [result, setResult] = useState<{
    path: string;
    content: string;
    image?: string;
    error?: string;
  } | null>(null);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<"preview" | "source">("preview");
  const [wrap, setWrap] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const navigated = useRef<{
    target: FileTarget;
    source: boolean;
    file: typeof result;
  } | null>(null);
  const { isCopied, copyToClipboard } = useCopyToClipboard();
  const pathCopy = useCopyToClipboard();
  const extension = target.path.split(".").at(-1)?.toLowerCase() || "";
  const markdown = ["md", "mdx", "markdown"].includes(extension);
  const source = !markdown || mode === "source";
  const file = result?.path === target.path ? result : null;
  useEffect(() => {
    setMode(target.line ? "source" : "preview");
  }, [target]);
  useEffect(() => {
    let current = true;
    setLoading(true);
    setResult(null);
    void native("fs/readFile", { path: target.path })
      .then((data) => {
        if (!current) return;
        if (typeof data.dataBase64 !== "string")
          throw new Error("没有收到文件内容");
        const mime = imageTypes[extension];
        setResult({
          path: target.path,
          content: mime ? "" : decode64(data.dataBase64),
          ...(mime ? { image: `data:${mime};base64,${data.dataBase64}` } : {}),
        });
      })
      .catch((error) => {
        if (current)
          setResult({ path: target.path, content: "", error: String(error) });
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [target.path, revision, extension]);
  useEffect(() => {
    const container = scroll.current;
    if (!container || !file || loading || !source || !active) return;
    if (
      navigated.current?.target === target &&
      navigated.current.file === file &&
      navigated.current.source === source
    )
      return;
    const lineCount = file.content.split("\n").length;
    const row = container.querySelector<HTMLElement>(
      `[data-line="${Math.min(target.line || 1, lineCount)}"]`,
    );
    if (row) {
      container.scrollTop +=
        row.getBoundingClientRect().top -
        container.getBoundingClientRect().top -
        (target.line ? container.clientHeight / 2 : 0);
      navigated.current = { target, file, source };
    }
  }, [file, loading, target, source, active]);
  return (
    <section className="workspace-file-view" aria-label="文件预览">
      <div className="workspace-file-toolbar">
        <FileText size={14} />
        <span title={target.path} className="workspace-file-path">
          {target.path}
        </span>
        {markdown && (
          <div
            className="workspace-file-modes"
            role="group"
            aria-label="文件显示方式"
          >
            <button aria-pressed={!source} onClick={() => setMode("preview")}>
              预览
            </button>
            <button aria-pressed={source} onClick={() => setMode("source")}>
              源码
            </button>
          </div>
        )}
        {!file?.image && (
          <TooltipIconButton
            tooltip="自动换行"
            aria-pressed={wrap}
            onClick={() => setWrap(!wrap)}
          >
            <WrapText />
          </TooltipIconButton>
        )}
        <TooltipIconButton
          tooltip={isCopied ? "已复制文件内容" : "复制文件内容"}
          disabled={loading || !file || !!file.error || !!file.image}
          onClick={() => void copyToClipboard(file?.content || "")}
        >
          {isCopied ? <Check /> : <Copy />}
        </TooltipIconButton>
        <TooltipIconButton
          tooltip="重新读取文件"
          disabled={loading}
          onClick={() => setRevision(revision + 1)}
        >
          <RefreshCw />
        </TooltipIconButton>
      </div>
      <div className="workspace-file-scroll" ref={scroll} data-wrap={wrap}>
        {loading || !file ? (
          <div className="workbench-empty" role="status">
            <LoaderCircle className="animate-spin" />
            正在读取文件…
          </div>
        ) : file.error ? (
          <div className="workspace-file-error" role="alert">
            <p>无法打开此文件</p>
            <p>{file.error}</p>
            <button onClick={() => setRevision(revision + 1)}>重试</button>
          </div>
        ) : file.image ? (
          <img
            className="workspace-file-image"
            src={file.image}
            alt={target.path.split("/").at(-1)}
          />
        ) : !source ? (
          <WorkspaceFileScope
            cwd={target.path.split("/").slice(0, -1).join("/") || "/"}
          >
            <div className="workspace-markdown aui-md">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  a: ({ href, children }) => (
                    <WorkspaceLink href={href}>{children}</WorkspaceLink>
                  ),
                  img: ({ src, alt }) => (
                    <WorkspaceImageLink
                      src={typeof src === "string" ? src : undefined}
                      alt={alt}
                    />
                  ),
                }}
              >
                {file.content}
              </ReactMarkdown>
            </div>
          </WorkspaceFileScope>
        ) : (
          <Light
            language={languages[extension] || "text"}
            useInlineStyles={false}
            showLineNumbers
            wrapLines
            wrapLongLines={wrap}
            className="workspace-file-source"
            lineNumberStyle={{
              minWidth: "4em",
              paddingRight: "1.5em",
              opacity: 0.45,
              userSelect: "none",
            }}
            lineProps={(line) => ({
              className: "workspace-source-line",
              "data-line": line,
              "data-selected":
                !!target.line &&
                line >= target.line &&
                line <= (target.endLine || target.line),
            })}
          >
            {file.content}
          </Light>
        )}
      </div>
      {file && !loading && !file.error && (
        <div className="workspace-file-status">
          <span>
            {file.image ? "图片" : `${file.content.split("\n").length} 行`}
          </span>
          {target.line && (
            <span>
              第 {target.line}
              {target.endLine ? `–${target.endLine}` : ""} 行
              {target.line > file.content.split("\n").length
                ? "（超出文件范围）"
                : ""}
            </span>
          )}
          <button onClick={() => pathCopy.copyToClipboard(target.path)}>
            {pathCopy.isCopied ? "已复制路径" : "复制路径"}
          </button>
        </div>
      )}
    </section>
  );
}
