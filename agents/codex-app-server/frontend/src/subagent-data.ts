import type { Obj } from "./native";
import type { Step } from "./types";

export const subagentSources = ["subAgent", "subAgentReview", "subAgentCompact", "subAgentThreadSpawn", "subAgentOther"];

export function agentPath(thread: Obj): string {
  return thread.source?.subAgent?.thread_spawn?.agent_path || thread.agentPath || "";
}

export function agentTitle(thread: Obj): string {
  const task = agentPath(thread).split("/").filter(Boolean).at(-1);
  return thread.name || (task && task.replaceAll("_", " ").replace(/^./, c => c.toUpperCase()))
    || thread.agentNickname || thread.preview?.slice(0, 70) || `子 Agent · ${String(thread.id).slice(-6)}`;
}

export function agentStatus(thread: Obj, turns: Obj[] = [], activity?: Obj): { label: string; running: boolean } {
  const type = typeof thread.status === "string" ? thread.status : thread.status?.type;
  const turn = turns.at(-1);
  const flags = thread.status?.activeFlags || [];
  if (flags.includes("waitingOnApproval") || flags.includes("waitingOnUserInput")) return { label: "需要你处理", running: true };
  if (type === "active" || turn?.status === "inProgress") return { label: "运行中", running: true };
  if (type === "systemError" || turn?.status === "failed" || activity?.status === "errored") return { label: "失败", running: false };
  if (turn?.status === "interrupted" || activity?.status === "interrupted") return { label: "已中断", running: false };
  if (turn?.status === "completed" || activity?.status === "completed") return { label: "已完成", running: false };
  if (type === "notLoaded") return { label: "未运行", running: false };
  if (type === "idle") return { label: "空闲", running: false };
  const labels: Obj = { started: "已启动", running: "运行中", pendingInit: "正在启动", shutdown: "已关闭", notFound: "未找到" };
  return { label: labels[activity?.status] || "状态未知", running: ["running", "pendingInit"].includes(activity?.status) };
}

export function agentActivities(steps: Step[] = []): Map<string, Obj> {
  const agents = new Map<string, Obj>();
  for (const step of steps) {
    const detail = step.detail as Obj | undefined;
    if (!detail) continue;
    if (step.type === "subAgentActivity" && detail.agentThreadId) {
      const previous = agents.get(detail.agentThreadId) || {};
      agents.set(detail.agentThreadId, { ...previous, path: detail.agentPath || previous.path,
        ...(detail.kind !== "interacted" ? { status: detail.kind } : {}) });
    }
    if (step.type === "collabAgentToolCall") {
      for (const id of new Set<string>([...(detail.receiverThreadIds || []), ...Object.keys(detail.agentsStates || {})])) {
        agents.set(id, { ...agents.get(id), ...detail.agentsStates?.[id], ...(detail.prompt ? { prompt: detail.prompt } : {}) });
      }
    }
  }
  return agents;
}

export function mergeAgentTurns(older: Obj[], latest: Obj[]): Obj[] {
  const rows = new Map(older.map(turn => [turn.id, turn]));
  for (const turn of latest) rows.set(turn.id, turn);
  return [...rows.values()].sort((a, b) => (a.startedAt || 0) - (b.startedAt || 0));
}

export function openAgentThread(threadId: string) {
  window.dispatchEvent(new CustomEvent("codex-agent", { detail: { threadId } }));
}
