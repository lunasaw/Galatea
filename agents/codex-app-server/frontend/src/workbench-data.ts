import type { Snapshot } from "./types";

type FileChange = { path: string; diff: string };
const changes = (detail: unknown): FileChange[] => {
  const value = detail as { changes?: unknown } | null;
  return Array.isArray(value?.changes)
    ? value.changes.filter(
        (entry): entry is FileChange =>
          typeof entry?.path === "string" && typeof entry?.diff === "string",
      )
    : [];
};

export function reviewTurns(snapshot: Snapshot | null) {
  const ids = new Set(
    (snapshot?.steps || [])
      .filter(
        (step) =>
          step.type === "fileChange" &&
          step.turn_id &&
          changes(step.detail).length,
      )
      .map((step) => step.turn_id!),
  );
  return [...ids].map((id) => ({ id }));
}

export function reviewFiles(
  snapshot: Snapshot | null,
  turnId: string | undefined,
) {
  const files = new Map<
    string,
    { path: string; diff: string; additions: number; removals: number }
  >();
  if (!turnId) return [];
  for (const step of snapshot?.steps || []) {
    if (step.type !== "fileChange" || step.turn_id !== turnId) continue;
    for (const change of changes(step.detail)) {
      const file = files.get(change.path) || {
        path: change.path,
        diff: "",
        additions: 0,
        removals: 0,
      };
      file.diff += (file.diff ? "\n" : "") + change.diff;
      for (const line of change.diff.split("\n")) {
        if (line.startsWith("+") && !line.startsWith("+++")) file.additions++;
        if (line.startsWith("-") && !line.startsWith("---")) file.removals++;
      }
      files.set(change.path, file);
    }
  }
  return [...files.values()];
}
