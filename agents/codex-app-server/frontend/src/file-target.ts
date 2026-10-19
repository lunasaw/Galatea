export type FileTarget = { path: string; line?: number; endLine?: number };

/** Resolve local Markdown/native file references without treating web URLs as files. */
export function localFileTarget(
  href: string | undefined,
  cwd = "",
): FileTarget | null {
  if (!href) return null;
  let value: string;
  try {
    value = decodeURIComponent(href);
  } catch {
    return null;
  }
  if (/[\x00-\x1f\x7f]/.test(value) || value.startsWith("#")) return null;
  if (
    /^(?:https?|mailto|javascript|data|vbscript|ftp|tel|app|codex):/i.test(
      value,
    )
  )
    return null;
  if (value.startsWith("file://"))
    value = value.replace(/^file:\/\/(?:localhost)?(?=\/)/, "");
  if (value.startsWith("//")) return null;
  const match = value.match(/^(.*?)(?:#L(\d+)(?:-L?(\d+))?|:(\d+)(?::\d+)?)?$/);
  if (!match || !match[1] || match[1].includes("#")) return null;
  const rawPath = match[1];
  if (/^[a-z][a-z\d+.-]*:/i.test(rawPath)) return null;
  if (!rawPath.startsWith("/") && !cwd.startsWith("/")) return null;
  const parts: string[] = [];
  for (const part of (rawPath.startsWith("/")
    ? rawPath
    : `${cwd}/${rawPath}`
  ).split("/")) {
    if (part === "..") parts.pop();
    else if (part && part !== ".") parts.push(part);
  }
  if (!parts.length) return null;
  const line = Number(match[2] || match[4]);
  const endLine = Number(match[3]);
  return {
    path: `/${parts.join("/")}`,
    ...(Number.isSafeInteger(line) && line > 0 ? { line } : {}),
    ...(Number.isSafeInteger(endLine) && endLine >= line && line > 0
      ? { endLine }
      : {}),
  };
}
