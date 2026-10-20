import type { ConsoleState } from "./store";
import type { Obj } from "./native";

/** Explicit tool presentation metadata is the signal, never keywords in chat text. */
export function conversationApp(state: Pick<ConsoleState, "active" | "sessions" | "snapshot">): Obj | undefined {
  const session = state.sessions.find(s => s.id === state.active);
  if (!session?.thread_id) return;
  const steps = [...(state.snapshot?.steps || []), ...(state.snapshot?.app_step ? [state.snapshot.app_step] : [])].sort((a, b) =>
    a.first_seq.localeCompare(b.first_seq, undefined, { numeric: true }));
  for (const step of steps.reverse()) {
    if (step.type === "tool") {
      const detail = step.detail as Obj | undefined;
      const result = step.result as Obj | undefined;
      if (step.success === false || detail?.success === false || result?.success === false) continue;
      for (const item of result?.contentItems || detail?.contentItems || []) {
        if (item.type !== "inputText" || typeof item.text !== "string") continue;
        let envelope: Obj;
        try { envelope = JSON.parse(item.text); } catch { continue; }
        if (!envelope || typeof envelope !== "object") continue;
        const view = envelope.schema_version === "codex-agent.tools/v1" && envelope.ok === true
          ? envelope.data?.app_view : undefined;
        if (!view || typeof view.server !== "string" || typeof view.resource_uri !== "string"
            || !view.resource_uri.startsWith("ui://")) continue;
        return {
          activation_id: step.id,
          event_cursor: `${state.snapshot?.epoch}:${step.first_seq}`,
          session_id: session.id,
          thread_id: session.thread_id,
          app: { server: view.server, uri: view.resource_uri },
          arguments: view.arguments || {},
          status: "completed",
        };
      }
      continue;
    }
    if (step.type !== "mcpToolCall") continue;
    const detail = step.detail as Obj | undefined;
    const uri = detail?.mcpAppUi?.resourceUri || detail?.appContext?.resourceUri || detail?.mcpAppResourceUri;
    if (typeof uri !== "string" || !uri.startsWith("ui://") || !detail?.server) continue;
    if (detail.error || detail.result?.isError || ["failed", "cancelled", "interrupted"].includes(detail.status)) continue;
    return {
      activation_id: step.id,
      event_cursor: `${state.snapshot?.epoch}:${step.first_seq}`,
      session_id: session.id,
      thread_id: session.thread_id,
      app: { server: detail.server, uri },
      tool: detail.tool,
      arguments: detail.arguments || {},
      result: detail.result,
      status: detail.status,
    };
  }
}
