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
              row.status !== "completed" &&
              turns.get(row.turn_id || "") === "inProgress"
                ? { type: "running" as const }
                : { type: "complete" as const, reason: "stop" as const },
          }
        : {}),
      metadata: {
        custom: {
          turnId: row.turn_id,
          phase: row.phase,
          itemStatus: row.status,
        },
      },
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
        "turn/plan/updated",
        "contextCompaction",
        "warning",
        "error",
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
            isError: toolState(step) === "failed",
          },
        ],
        status:
          toolState(step) === "running" &&
          turns.get(step.turn_id || "") === "inProgress"
            ? { type: "running" }
            : { type: "complete", reason: "stop" },
        metadata: { custom: { step, turnId: step.turn_id } },
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
      metadata: { custom: { turnId: step.turn_id } },
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
  const detail = object(step.detail);
  if (step.type === "error") return "failed";
  if (step.type === "turn/plan/updated" && Array.isArray(detail.plan))
    return detail.plan.every((item) => object(item).status === "completed")
      ? "complete"
      : "unknown";
  if (
    step.success === false ||
    ["failed", "declined", "cancelled", "interrupted"].includes(
      step.status || "",
    ) ||
    detail.error ||
    (typeof detail.exitCode === "number" && detail.exitCode !== 0)
  )
    return "failed";
  if (step.success === true || step.status === "completed") return "complete";
  if (step.status === "inProgress" || step.status === "executing")
    return "running";
  return "unknown";
}

// Preserve native item identities inside a turn's expandable activity. Legacy
// projections lack phase: only infer a final answer after a completed turn.
export function toConversationMessages(
  snapshot: Snapshot | null,
): ThreadMessageLike[] {
  const rows = toMessages(snapshot);
  const rawTurns = snapshot?.turns?.map((turn) => {
    const detail = object(turn.detail);
    return {
      ...turn,
      started_at:
        turn.started_at ||
        (typeof detail.startedAt === "number"
          ? new Date(detail.startedAt * 1000).toISOString()
          : undefined),
      completed_at:
        turn.completed_at ||
        (typeof detail.completedAt === "number"
          ? new Date(detail.completedAt * 1000).toISOString()
          : undefined),
    };
  });
  const turns = new Map(rawTurns?.map((turn) => [turn.id, turn]));
  const lastText = new Map<string, string>();
  for (const row of rows) {
    const meta = row.metadata?.custom;
    if (
      row.role === "assistant" &&
      !meta?.step &&
      Array.isArray(row.content) &&
      row.content.some((p) => p.type === "text") &&
      meta?.turnId
    )
      lastText.set(String(meta.turnId), row.id!);
  }
  const result: ThreadMessageLike[] = [];
  for (const row of rows) {
    const meta = row.metadata?.custom || {};
    const turnId = String(meta.turnId || "");
    const turn = turns.get(turnId);
    const isText =
      Array.isArray(row.content) &&
      row.content.some((p) => p.type === "text") &&
      !meta.step;
    const final =
      meta.phase === "final_answer" ||
      (!meta.phase &&
        isText &&
        turn?.status === "completed" &&
        lastText.get(turnId) === row.id);
    if (row.role !== "assistant" || final || !turnId) {
      result.push({ ...row, metadata: { custom: { ...meta, final, turn } } });
      continue;
    }
    const previous = result.at(-1);
    const step = meta.step as Step | undefined;
    const toolId = step?.call_id || step?.id;
    const stepMap = step && toolId ? { [toolId]: step } : {};
    if (
      previous?.metadata?.custom?.activity &&
      previous.metadata.custom.turnId === turnId
    ) {
      result[result.length - 1] = {
        ...previous,
        content: [
          ...(previous.content as Exclude<
            ThreadMessageLike["content"],
            string
          >),
          ...(row.content as Exclude<ThreadMessageLike["content"], string>),
        ],
        metadata: {
          custom: {
            ...previous.metadata.custom,
            steps: {
              ...(previous.metadata.custom.steps as Record<string, Step>),
              ...stepMap,
            },
            itemCount: Number(previous.metadata.custom.itemCount) + 1,
          },
        },
      };
    } else {
      result.push({
        ...row,
        id: `activity:${row.id}`,
        metadata: {
          custom: {
            activity: true,
            turnId,
            turn,
            steps: stepMap,
            itemCount: 1,
          },
        },
        status:
          turn?.status === "inProgress"
            ? { type: "running" }
            : { type: "complete", reason: "stop" },
      });
    }
  }
  const lastActivity = new Map<string, number>();
  const lastItem = new Map<string, number>();
  result.forEach((row, index) => {
    const meta = row.metadata?.custom;
    if (meta?.turnId) lastItem.set(String(meta.turnId), index);
    if (meta?.activity) lastActivity.set(String(meta.turnId), index);
  });
  return result.map((row, index) => {
    const meta = row.metadata?.custom;
    if (!meta?.activity) return row;
    const turnId = String(meta.turnId);
    const active =
      turns.get(turnId)?.status === "inProgress" &&
      lastItem.get(turnId) === index;
    return {
      ...row,
      status: active
        ? { type: "running" as const }
        : { type: "complete" as const, reason: "stop" as const },
      metadata: {
        custom: { ...meta, lastActivity: lastActivity.get(turnId) === index },
      },
    };
  });
}
