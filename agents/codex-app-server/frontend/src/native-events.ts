import { api } from "./store";
import type { Obj } from "./native";

/** Subscribe before issuing the native operation, so its first event cannot race the cursor. */
export async function observeNative(method: string, receive: (params: Obj) => void, fail: (error: unknown) => void) {
  let cursor = (await api<Obj>("/api/events/history?limit=1")).watermark;
  let stopped = false; let timer: ReturnType<typeof setTimeout> | undefined;
  const poll = async () => {
    try {
      let more = true;
      while (more && !stopped) {
        const page = await api<Obj>(`/api/events/history?limit=200&method=${encodeURIComponent(method)}&after=${encodeURIComponent(cursor)}`);
        if (stopped) return;
        if (page.coverage?.status !== "complete") throw new Error("事件历史不完整，请刷新状态");
        for (const event of page.events) if (event.payload?.params) receive(event.payload.params);
        cursor = page.next_cursor; more = page.has_more;
      }
    } catch (error) { if (!stopped) fail(error); }
    if (!stopped) timer = setTimeout(poll, 500);
  };
  void poll();
  return () => { stopped = true; clearTimeout(timer); };
}
