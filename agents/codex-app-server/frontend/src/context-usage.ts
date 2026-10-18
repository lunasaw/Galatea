import type { Snapshot } from "./types";

const nonnegative = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
export function contextUsage(snapshot: Snapshot | null) {
  const usage = snapshot?.turns?.slice().reverse().find(turn => turn.usage)?.usage as {
    last?: { totalTokens?: number }; total?: { totalTokens?: number }; modelContextWindow?: number;
  } | undefined;
  const used = nonnegative(usage?.last?.totalTokens);
  const capacity = nonnegative(usage?.modelContextWindow);
  const window = capacity && capacity > 0 ? capacity : null;
  return { used, window, total: nonnegative(usage?.total?.totalTokens),
    percent: used !== null && window !== null ? Math.min(100, Math.round(used / window * 100)) : null };
}

export function contextWarning(percent: number | null) {
  return percent === null ? "" : percent >= 95 ? "上下文接近容量上限，可能需要压缩。"
    : percent >= 80 ? "上下文占用较高，后续对话可能需要压缩。" : "";
}
