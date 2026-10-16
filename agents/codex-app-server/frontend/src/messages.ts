import type { ThreadMessageLike } from "@assistant-ui/react";
import type { Snapshot, Step } from "./types";

export function toMessages(snapshot: Snapshot | null): ThreadMessageLike[] {
  if (!snapshot) return [];
  const turns = new Map(snapshot.turns?.map((turn) => [turn.id, turn.status]));
  const rows: { seq: string; order?: number; message: ThreadMessageLike }[] = (
    snapshot.messages || []
  ).map((row) => ({
    seq: row.first_seq,
    order: row.replay_order,
    message: {
      id: row.id,
      role: row.role,
      content: [
        { type: "text", text: row.text || "" },
        ...(row.content || []).flatMap((part) =>
          part.type === "image" &&
          part.url &&
          /^(data:image\/(png|jpeg|webp|gif);base64,|https?:\/\/)/i.test(
            part.url,
          )
            ? [{ type: "image" as const, image: part.url }]
            : [],
        ),
      ],
      ...(row.role === "assistant"
        ? {
            status:
              turns.get(row.turn_id || "") === "inProgress"
                ? { type: "running" as const }
                : { type: "complete" as const, reason: "stop" as const },
          }
        : {}),
    },
  }));
  for (const step of snapshot.steps || []) {
    if (
      step.type !== "tool" &&
      ![
        "commandExecution",
        "fileChange",
        "mcpToolCall",
        "webSearch",
        "collabAgentToolCall",
        "imageGeneration",
        "plan",
      ].includes(step.type)
    )
      continue;
    rows.push({
      seq: step.first_seq,
      order: step.replay_order,
      message: {
        id: step.id,
        role: "assistant",
        content: [
          {
            type: "tool-call",
            toolCallId: step.call_id || step.id,
            toolName: step.tool || step.type || "工具调用",
            argsText:
              typeof step.arguments === "string"
                ? step.arguments
                : JSON.stringify(step.arguments ?? step.detail ?? {}),
            ...(step.result !== undefined
              ? { result: step.result }
              : step.status === "completed"
                ? { result: step.detail }
                : {}),
            isError: step.success === false,
          },
        ],
        status:
          toolState(step) === "running" &&
          turns.get(step.turn_id || "") === "inProgress"
            ? { type: "running" }
            : { type: "complete", reason: "stop" },
        metadata: { custom: { step } },
      },
    });
  }
  rows.push(...reasoningMessages(snapshot));
  return rows
    .sort((a, b) =>
      BigInt(a.seq) < BigInt(b.seq)
        ? -1
        : BigInt(a.seq) > BigInt(b.seq)
          ? 1
          : (a.order ?? 0) - (b.order ?? 0),
    )
    .map((row) => row.message);
}

// Read only public summaries. Older running Hosts use separate delta/lifecycle
// rows; reconcile those too, without requiring a restart that loses history.
function reasoningMessages(snapshot: Snapshot) {
  const items = new Map<string, { step: Step; delta?: Step }>();
  for (const step of snapshot.steps || []) {
    if (step.type === "reasoning") items.set(step.id, { step });
  }
  for (const delta of snapshot.steps || []) {
    if (delta.type !== "item/reasoning/summaryTextDelta") continue;
    const detail = object(delta.detail);
    const id =
      detail.itemId && detail.threadId
        ? `item:${detail.threadId}:${delta.turn_id}:${detail.itemId}`
        : delta.id;
    const current = items.get(id);
    items.set(id, { step: current?.step || { ...delta, id }, delta });
  }
  return [...items.values()].flatMap(({ step, delta }) => {
    const detail = object(step.detail);
    const final = Array.isArray(detail.summary)
      ? detail.summary
          .filter((text): text is string => typeof text === "string")
          .join("\n\n")
      : "";
    const streamed = Object.entries(step)
      .filter(
        ([key, value]) =>
          /^summary_\d+$/.test(key) && typeof value === "string",
      )
      .sort(([a], [b]) => Number(a.slice(8)) - Number(b.slice(8)))
      .map(([, value]) => value)
      .join("\n\n");
    const live = streamed || delta?.text || "";
    const text = step.status === "completed" ? final || live : live || final;
    if (!text.trim()) return [];
    const turn = snapshot.turns?.find((turn) => turn.id === step.turn_id);
    const lastSeq = BigInt(delta?.last_seq || step.last_seq);
    const advanced = [
      ...(snapshot.messages || []),
      ...(snapshot.steps || []),
    ].some(
      (row) =>
        row.turn_id === step.turn_id &&
        BigInt(row.first_seq) > lastSeq &&
        ("role" in row
          ? row.role === "assistant"
          : row.type === "tool" || row.type === "reasoning"),
    );
    const running =
      turn?.status === "inProgress" && step.status !== "completed" && !advanced;
    const complete =
      step.status === "completed" || advanced || turn?.status === "completed";
    const startedAt = Date.parse(step.started_at || "");
    const completedAt = Date.parse(step.completed_at || "");
    const timing = Number.isFinite(startedAt)
      ? {
          startedAt,
          ...(Number.isFinite(completedAt) && completedAt >= startedAt
            ? { completedAt }
            : {}),
        }
      : undefined;
    const message: ThreadMessageLike = {
      id: step.id,
      role: "assistant",
      content: [{ type: "reasoning", text, ...(timing ? { timing } : {}) }],
      status: running
        ? { type: "running" }
        : complete
          ? { type: "complete", reason: "stop" }
          : { type: "incomplete", reason: "cancelled" },
    };
    const seq =
      delta && BigInt(delta.first_seq) < BigInt(step.first_seq)
        ? delta.first_seq
        : step.first_seq;
    return [{ seq, order: step.replay_order, message }];
  });
}

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
}
export function toolState(
  step: Step,
): "running" | "complete" | "failed" | "unknown" {
  if (step.success === false || step.status === "failed") return "failed";
  if (step.success === true || step.status === "completed") return "complete";
  if (step.status === "inProgress" || step.status === "executing")
    return "running";
  return "unknown";
}
