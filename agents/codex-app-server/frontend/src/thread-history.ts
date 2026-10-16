export type NativeTurn = { id: string; status?: string; text: string };
type Caller = { nativeCall(method: string, params: unknown): Promise<unknown> };
export async function readTurns(caller: Caller, threadId: string) {
  const { thread } = (await caller.nativeCall("thread/read", {
    threadId,
    includeTurns: true,
  })) as any;
  if (!thread || !Array.isArray(thread.turns))
    throw new Error("无法读取完整会话历史");
  const turns: NativeTurn[] = thread.turns.map((turn: any) => ({
    id: turn.id,
    status: turn.status,
    text: (turn.items || [])
      .filter((item: any) => item.type === "userMessage")
      .flatMap((item: any) => item.content || [])
      .filter((p: any) => p.type === "text")
      .map((p: any) => p.text)
      .join(" ")
      .slice(0, 120),
  }));
  return { historyMode: thread.historyMode, turns };
}
export function rollbackRequest(
  threadId: string,
  history: { historyMode?: string; turns: NativeTurn[] },
  beforeTurnId: string,
) {
  const index = history.turns.findIndex((turn) => turn.id === beforeTurnId);
  if (index < 0) throw new Error("所选轮次已不存在，请重新打开回退菜单");
  if (history.turns.some((turn) => turn.status === "inProgress"))
    throw new Error("请先停止当前回复，再回退会话");
  return history.historyMode === "paginated"
    ? { method: "thread/revert", params: { threadId, beforeTurnId } }
    : {
        method: "thread/rollback",
        params: { threadId, numTurns: history.turns.length - index },
      };
}
