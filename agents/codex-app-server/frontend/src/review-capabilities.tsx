import { useCallback, useState } from "react";
import { Button } from "./components/ui/button";
import { Feedback, Facts, useAction, useResource } from "./capability-controls";
import { type Obj } from "./native";
import { api, consoleStore as store, type ConsoleState } from "./store";
import { sharedMutation } from "./native-mutation";

const approvedReviews = new Set<string>();

export function guardianEvent(notification: Obj) {
  const snake = (value: any): any => Array.isArray(value) ? value.map(snake) : value && typeof value === "object"
    ? Object.fromEntries(Object.entries(value).map(([k, v]) => [k.replace(/[A-Z]/g, (c) => "_" + c.toLowerCase()), snake(v)])) : value;
  const action = snake(notification.action);
  for (const key of ["type", "source", "protocol"]) if (typeof action[key] === "string") action[key] = action[key].replace(/[A-Z]/g, (c: string) => "_" + c.toLowerCase());
  return { id: notification.reviewId, target_item_id: notification.targetItemId, turn_id: notification.turnId,
    started_at_ms: notification.startedAtMs, completed_at_ms: notification.completedAtMs,
    status: notification.review.status.replace(/[A-Z]/g, (c: string) => "_" + c.toLowerCase()),
    risk_level: notification.review.riskLevel, user_authorization: notification.review.userAuthorization,
    rationale: notification.review.rationale, decision_source: notification.decisionSource, action };
}
export function GuardianReview({ notification }: { notification: Obj }) {
  const identity = `${notification.threadId}:${notification.reviewId}`;
  const action = useAction(); const [approved, setApproved] = useState(approvedReviews.has(identity));
  const labels: Obj = { inProgress: "自动审批中", approved: "自动审批已通过", denied: "自动审批已拒绝", timedOut: "自动审批超时", aborted: "自动审批已取消" };
  return <section className="native-card guardian-review"><h4>{labels[notification.review?.status] || "自动审批"}</h4>
    <Facts value={notification.action} /><p>{notification.review?.rationale}</p>
    {notification.review?.riskLevel && <small>风险等级：{notification.review.riskLevel}</small>}
    {notification.review?.status === "denied" && <><p className="muted">批准后允许该操作重试一次，重试仍会经过自动审批。</p>
      <Button variant="outline" disabled={action.busy || approved} onClick={() => void action.run(async () => {
        await sharedMutation(`guardian:${identity}`).run("thread/approveGuardianDeniedAction", { threadId: notification.threadId, event: guardianEvent(notification) }); approvedReviews.add(identity); setApproved(true); store.schedule();
      }, "已批准一次重试")}>{approved ? "已批准" : "批准一次重试"}</Button></>}
    <Feedback error={action.error} status={action.status} />
  </section>;
}
export function ApprovalRecords({ state }: { state: ConsoleState }) {
  const session = state.sessions.find((s) => s.id === state.active);
  return <section className="capability-page"><h3>审批记录</h3><p>{session ? `当前项目 · ${session.cwd || store.cwd}` : "请先选择会话"}</p>
    {session && <ApprovalHistory key={session.id} sessionId={session.id} />}
    <h4>当前会话的自动审批记录</h4>{state.snapshot?.steps?.filter((s) => s.type === "guardian").map((s) => <GuardianReview key={s.id} notification={s.detail as Obj} />)}
  </section>;
}

const approvalMethods: Obj = {
  "item/commandExecution/requestApproval": "命令执行", "item/fileChange/requestApproval": "文件修改",
  "item/permissions/requestApproval": "额外权限", execCommandApproval: "命令执行", applyPatchApproval: "文件修改",
};
function ApprovalHistory({ sessionId }: { sessionId: string }) {
  const history = useResource(useCallback(async () => {
    const rows = (await Promise.all(Object.keys(approvalMethods).map(async (method) => {
      const collected: Obj[] = []; let after = ""; let until = "";
      do {
        const query = new URLSearchParams({ method, limit: "200", ...(after ? { after, until } : {}) });
        const page = await api<Obj>(`/api/sessions/${encodeURIComponent(sessionId)}/history?${query}`);
        if (page.coverage?.status !== "complete") throw new Error("审批记录不完整，请刷新后重试");
        collected.push(...page.events);
        if (!page.has_more) break;
        if (after === page.next_cursor) throw new Error("审批记录游标未前进");
        after = page.next_cursor; until = page.watermark;
      } while (true);
      return collected;
    }))).flat();
    const key = (row: Obj) => JSON.stringify([row.runtime_instance_id, row.rpc_id]);
    const responses = new Map(rows.filter((r) => r.kind === "response" && r.direction === "host_to_runtime").map((r) => [key(r), r.payload]));
    return rows.filter((r) => r.kind === "request" && r.direction === "runtime_to_host")
      .map((r): Obj => ({ ...r, response: responses.get(key(r)) })).sort((a, b) => b.observed_at.localeCompare(a.observed_at));
  }, [sessionId]), 10000);
  const decisions: Obj = { accept: "已允许", acceptForSession: "已允许本会话", decline: "已拒绝", cancel: "已取消", approved: "已批准", approved_for_session: "已批准本会话", denied: "已拒绝", abort: "已中止" };
  return <section className="approval-history"><h4>审批记录</h4><Button variant="ghost" size="sm" onClick={() => void history.refresh()}>刷新审批记录</Button>
    <ul className="capability-list">{history.data?.map((row) => {
      const params = row.payload.params; const result = row.response?.result; const decision = result?.decision;
      const label = row.response?.error ? "请求已结束" : decision ? decisions[decision] || "已按指定规则批准" : result ? "已回应权限申请" : "未收到审批结果";
      return <li key={row.event_id}><strong>{approvalMethods[row.method]} · {label}</strong><small>{new Date(row.observed_at).toLocaleString()}</small>
        {params.command && <pre className="whitespace-pre-wrap">{Array.isArray(params.command) ? params.command.join(" ") : params.command}</pre>}
        {params.reason && <p>{params.reason}</p>}{params.cwd && <small>{params.cwd}</small>}
        <details><summary>审批范围与结果</summary><Facts value={params} />{row.response && <Facts value={row.response.error || result} />}</details>
      </li>;
    })}</ul>{history.data?.length === 0 && <p className="muted">当前聊天没有审批记录</p>}<Feedback error={history.error} />
  </section>;
}
