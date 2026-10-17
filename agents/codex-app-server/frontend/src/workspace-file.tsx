import { createContext, useContext, type ReactNode } from "react";
import { localFileTarget, type FileTarget } from "./file-target";
import { deferred } from "./deferred";
export { localFileTarget, type FileTarget } from "./file-target";
export const WorkspaceFileView = deferred(() => import("./workspace-file-view").then(m => ({ default: m.WorkspaceFileView })));

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
