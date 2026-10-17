import type { Obj } from "./native";

export type TranscriptState = { canonical: boolean; lines: Obj[] };
/** Native history items and flat compatibility notifications describe the same speech. */
export function transcriptEvent(state: TranscriptState, method: string, params: Obj): TranscriptState {
  if (method === "thread/realtime/item/transcript/delta") {
    return { canonical: true, lines: state.lines.filter((line) => line.id).map((line) => line.id === params.itemId ? { ...line, text: line.text + params.delta } : line) };
  }
  if (["thread/realtime/item/started", "thread/realtime/item/completed"].includes(method) && params.item?.type === "transcriptSegment") {
    const lines = state.lines.filter((line) => line.id);
    const item = { ...params.item, done: method.endsWith("/completed") };
    const index = lines.findIndex((line) => line.id === item.id);
    if (index < 0) lines.push(item); else lines[index] = item;
    return { canonical: true, lines };
  }
  if (state.canonical || !["thread/realtime/transcript/delta", "thread/realtime/transcript/done"].includes(method)) return state;
  const lines = [...state.lines]; const last = lines.at(-1);
  const done = method.endsWith("/done");
  const pending = !!last && last.role === params.role && !last.done;
  const item = { role: params.role, text: done ? params.text : (pending ? last.text : "") + params.delta, done };
  if (pending) lines[lines.length - 1] = item; else lines.push(item);
  return { canonical: false, lines };
}
