import type { Snapshot, Step } from "./types";

export type ReviewFile = {
  path: string;
  oldPath?: string;
  diff: string;
  additions: number;
  removals: number;
  kind?: string;
};
export type DiffLine = {
  kind: "add" | "remove" | "context" | "hunk" | "meta";
  text: string;
  oldLine?: number;
  newLine?: number;
};

// Hunk lengths distinguish source lines beginning with ---/+++ from headers.
export function diffLines(diff: string): DiffLine[] {
  let oldLine: number | undefined, newLine: number | undefined;
  let oldRemaining = 0, newRemaining = 0;
  const hasHunks = /^@@ /m.test(diff);
  return diff.replace(/\n$/, "").split("\n").map((text): DiffLine => {
    const hunk = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(text);
    if (hunk) {
      oldLine = Number(hunk[1]); newLine = Number(hunk[3]);
      oldRemaining = Number(hunk[2] ?? 1); newRemaining = Number(hunk[4] ?? 1);
      return { kind: "hunk", text };
    }
    const inHunk = oldRemaining > 0 || newRemaining > 0;
    if (inHunk || (!hasHunks && !/^(---|\+\+\+) /.test(text))) {
      if (text.startsWith("+")) {
        newRemaining--; return { kind: "add", text: text.slice(1), newLine: newLine === undefined ? undefined : newLine++ };
      }
      if (text.startsWith("-")) {
        oldRemaining--; return { kind: "remove", text: text.slice(1), oldLine: oldLine === undefined ? undefined : oldLine++ };
      }
      if (text.startsWith(" ")) {
        oldRemaining--; newRemaining--;
        return { kind: "context", text: text.slice(1), oldLine: oldLine === undefined ? undefined : oldLine++, newLine: newLine === undefined ? undefined : newLine++ };
      }
    }
    return { kind: "meta", text };
  });
}

function file(path: string, diff: string, extra: Partial<ReviewFile> = {}): ReviewFile {
  const lines = diffLines(diff);
  return { path, diff, additions: lines.filter((l) => l.kind === "add").length,
    removals: lines.filter((l) => l.kind === "remove").length, ...extra };
}

function headerPath(header: string) {
  let path = header.trimEnd();
  if (path.startsWith('"')) {
    // Git quotes non-ASCII bytes as octal escapes; decode bytes as UTF-8.
    const bytes: number[] = [];
    const inner = path.slice(1, path.lastIndexOf('"'));
    for (let i = 0; i < inner.length;) {
      const octal = /^\\([0-7]{3})/.exec(inner.slice(i));
      if (octal) { bytes.push(parseInt(octal[1], 8)); i += 4; }
      else if (inner[i] === "\\" && i + 1 < inner.length) {
        const escaped = ({ t: "\t", n: "\n", r: "\r" } as Record<string, string>)[inner[i + 1]] ?? inner[i + 1];
        bytes.push(...new TextEncoder().encode(escaped)); i += 2;
      } else {
        const point = String.fromCodePoint(inner.codePointAt(i)!);
        bytes.push(...new TextEncoder().encode(point)); i += point.length;
      }
    }
    path = new TextDecoder().decode(new Uint8Array(bytes));
  } else path = path.split("\t")[0];
  return path === "/dev/null" ? path : path.replace(/^[ab]\//, "");
}

export function parseUnifiedDiff(diff: string): ReviewFile[] {
  if (!diff.trim()) return [];
  const blocks = diff.startsWith("diff --git ")
    ? diff.split(/(?=^diff --git )/m)
    : diff.split(/(?=^--- [^\n]+\n\+\+\+ )/m);
  return blocks.flatMap((block) => {
    const header = /^--- ([^\n]+)\n\+\+\+ ([^\n]+)/m.exec(block);
    if (!header) return [];
    const oldPath = headerPath(header[1]), newPath = headerPath(header[2]);
    const path = newPath === "/dev/null" ? oldPath : newPath;
    return [file(path, block, { oldPath, kind: oldPath === "/dev/null" ? "add" : newPath === "/dev/null" ? "delete" : "update" })];
  });
}

export function turnDiff(snapshot: Snapshot | null, turnId: string | undefined): string | undefined {
  const steps = (snapshot?.steps || []).filter((s) => s.type === "turnDiff" && s.turn_id === turnId);
  const latest = steps.reduce<Step | undefined>((a, b) => !a || BigInt(b.last_seq) >= BigInt(a.last_seq) ? b : a, undefined);
  const diff = (latest?.detail as { diff?: unknown })?.diff;
  return typeof diff === "string" ? diff : undefined;
}

export function reviewTurns(snapshot: Snapshot | null) {
  const ids = new Set((snapshot?.steps || []).filter((s) => s.type === "fileChange" || s.type === "turnDiff").map((s) => s.turn_id));
  return [...ids].filter((id): id is string => !!id && reviewFiles(snapshot, id).length > 0).map((id) => ({ id }));
}

export function reviewFiles(snapshot: Snapshot | null, turnId: string | undefined): ReviewFile[] {
  if (!turnId) return [];
  // A turn diff replaces previous updates, including an empty diff after edits cancel out.
  const aggregate = turnDiff(snapshot, turnId);
  if (aggregate !== undefined) return parseUnifiedDiff(aggregate);
  const files = new Map<string, ReviewFile>();
  for (const step of snapshot?.steps || []) {
    if (step.type !== "fileChange" || step.turn_id !== turnId) continue;
    const detail = step.detail as { changes?: unknown; status?: string } | null;
    if (["failed", "declined", "inProgress"].includes(step.status || detail?.status || "")) continue;
    if (!Array.isArray(detail?.changes)) continue;
    for (const change of detail.changes) {
      if (typeof change?.path !== "string" || typeof change.diff !== "string") continue;
      const path = change.kind?.move_path || change.path;
      let diff = change.diff;
      // Native add/delete items carry file contents, while updates carry a patch.
      if (["add", "delete"].includes(change.kind?.type) && !/^@@ |^diff --git /m.test(diff))
        diff = diff.replace(/\n$/, "").split("\n").map((line: string) => (change.kind.type === "add" ? "+" : "-") + line).join("\n");
      const previous = files.get(path);
      const current = file(path, diff, { kind: change.kind?.type });
      files.set(path, { ...current, diff: (previous ? previous.diff + "\n" : "") + diff,
        additions: (previous?.additions || 0) + current.additions,
        removals: (previous?.removals || 0) + current.removals });
    }
  }
  return [...files.values()];
}

export function relativeReviewPath(path: string, cwd: string) {
  const root = cwd.replace(/\/$/, "") + "/";
  return path.startsWith(root) ? path.slice(root.length) : path;
}
