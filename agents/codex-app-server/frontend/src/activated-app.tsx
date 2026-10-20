/** Generic, user-scoped activation channel. No business task model is imported. */
import { useEffect, useState } from "react";
import { api, consoleStore as store } from "./store";
import type { Obj } from "./native";
const activated = new Set<string>();
export function useActivatedApp(sessionId: string | null) {
  const [activation, setActivation] = useState<Obj>();
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    setActivation(undefined);
    const poll = async () => {
      if (!sessionId || disposed) return;
      try {
        const result = await api<{ items: Obj[] }>(
          "/api/mcp-app/activations?" +
            new URLSearchParams({ session_id: sessionId }),
        );
        if (disposed) return;
        const ready = result.items.filter(
          (x) => x.state === "ready" && x.app?.server && x.app?.uri,
        );
        const own = [...ready]
          .reverse()
          .find((x) => x.session_id === sessionId);
        setActivation(own);
        const pending = ready.find(
          (x) =>
            x.source_session_id === sessionId &&
            x.session_id !== sessionId &&
            !activated.has(x.activation_id),
        );
        if (
          pending &&
          !document.hidden &&
          store.getSnapshot().active === sessionId
        ) {
          await store.refreshSessions();
          if (disposed || store.getSnapshot().active !== sessionId) return;
          activated.add(pending.activation_id);
          await store.select(pending.session_id);
          return;
        }
      } catch {
        /* Optional integration: keep ordinary chats unaffected when unavailable. */
      }
      if (!disposed) timer = setTimeout(poll, 2500);
    };
    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [sessionId]);
  // Never render a previous chat's App during a session transition.
  return activation?.session_id === sessionId ? activation : undefined;
}
